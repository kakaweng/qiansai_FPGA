"""Feedback only: mirror the displayed decision; never grants driving permission."""
import socket
import threading
import time

OFF, PENDING, GO, STOP, YIELD, UNKNOWN, ALARM = range(7)
NAMES = ('OFF', 'PENDING', 'GO', 'STOP', 'YIELD', 'UNKNOWN', 'ALARM')


def feedback_state(model, view, scene=None, frozen=False, capture_status=None, gate=None):
    direction = view.get('direction', 0)
    if direction not in (1, 2, 3): direction = 0
    simulation = bool(scene and scene.get('enabled'))
    result = lambda mode, why, route=direction: (mode, route, simulation, why)
    # A frozen snapshot remains an explicit local simulation even if the live link ends.
    if simulation and frozen:
        if capture_status in ('IDLE', 'ANALYZING', 'ERROR'):
            return result(PENDING if capture_status == 'IDLE' and direction else UNKNOWN, 'snapshot_wait')
    elif not view.get('live'):
        return result(UNKNOWN, 'connection_or_video_stale')
    elif not view.get('running'):
        return result(OFF, 'detection_stopped')
    if view.get('lane_alarm') or (scene or {}).get('violation') or (model.get('p1') or {}).get('alarm'):
        return result(ALARM, 'violation_or_solid_request')
    if simulation:
        if not scene.get('fresh'):
            return result(UNKNOWN, 'scene_stale')
        phase = scene.get('phase')
        gate_status = (gate or {}).get('status')
        if phase == 'WAIT_PEDESTRIAN' or gate_status == 'YIELD':
            return result(YIELD, 'route_pedestrian_conflict')
        if phase in ('WAIT_RED', 'WAIT_YELLOW') or gate_status == 'STOP_SIGNAL':
            return result(STOP, 'signal_stop')
        if phase in ('WAIT_PERCEPTION', 'DISCONNECTED') or gate_status == 'UNKNOWN':
            return result(UNKNOWN, 'scene_evidence_unknown')
        if phase in ('PAUSED', 'COMPLETE'):
            return result(STOP, 'simulation_paused_or_complete')
        if scene.get('active') and scene.get('speed_mm_s', 0) > 0 and direction:
            return result(GO, 'simulated_movement')
        return result(PENDING if direction else UNKNOWN, 'route_selected')
    request = view.get('request', 0)
    p1 = model.get('p1') or {}
    reason = p1.get('reason')
    if request in (1, 2) and (model.get('line') or [0, 0])[request-1] == 1:
        return result(ALARM, 'solid_lane_request', 1 if request == 1 else 3)
    if reason == 'WAIT_PEDESTRIAN': return result(YIELD, 'pedestrian_yield')
    if reason in ('RED_STOP', 'YELLOW_WAIT'): return result(STOP, 'signal_stop')
    if request in (1, 2):
        side = request - 1
        line = (model.get('line') or [0, 0])[side]
        if line == 1: return result(ALARM, 'solid_lane_request', 1 if request == 1 else 3)
        # Dashed alone cannot establish side/rear clearance; blink means review the request.
        return result(PENDING if line == 2 else UNKNOWN, 'lane_boundary_review', 1 if request == 1 else 3)
    if reason in ('GREEN_GO', 'RIGHT_GO') and p1.get('led') in (1, 2, 3):
        return result(GO, 'board_go', p1['led'])
    if reason in ('GREEN_READY', 'AUTO_GREEN_READY', 'RIGHT_READY', 'SELECT_DIRECTION'):
        return result(PENDING if direction else UNKNOWN, 'waiting_for_request')
    return result(UNKNOWN, 'perception_or_signal_unknown')


class FeedbackPolicy:
    def __init__(self):
        self.alarm_until = 0.0
        self.alarm_direction = 0

    def update(self, state, now):
        mode, direction, simulated, reason = state
        if mode in (OFF, UNKNOWN):
            self.alarm_until = 0
        if mode == ALARM:
            self.alarm_until = now + 2
            self.alarm_direction = direction
        elif now < self.alarm_until:
            mode, direction, reason = ALARM, self.alarm_direction, 'alarm_minimum_hold'
        return mode, direction, simulated, reason


class FPGAFeedbackClient(threading.Thread):
    """Latest-state 5Hz heartbeat; network I/O never runs on the render thread."""
    def __init__(self, host='127.0.0.1', port=9017, active_low=False):
        super().__init__(daemon=True)
        self.host, self.port, self.active_low = host, port, bool(active_low)
        self.lock = threading.Lock()
        self.latest = (OFF, 0, False, 'startup')
        self.updated = time.monotonic()
        self.stop_event = threading.Event()
        self.policy = FeedbackPolicy()
        self.last_report = None

    def submit(self, dashboard):
        model, view = dashboard.last_model or {}, dashboard.last_view or {}
        scene = getattr(dashboard, 'scene', None)
        frozen = getattr(dashboard, 'freeze_mode', False)
        freezer = getattr(dashboard, 'freezer', None)
        gate = freezer.gate if frozen and freezer else (
            (getattr(dashboard, 'world_view', {}).get('presentation') or {}).get('route_gate'))
        state = feedback_state(model, view, scene, frozen,
                               getattr(freezer, 'status', None), gate)
        with self.lock:
            self.latest, self.updated = state, time.monotonic()

    def send(self, state):
        mode, direction, simulated, _ = state
        command = 0x4c310000 | (int(self.active_low) << 9) | (int(simulated) << 8) | (direction << 4) | mode
        with socket.create_connection((self.host, self.port), timeout=.35) as link:
            link.settimeout(.35)
            link.sendall(('LED1 %d %d %d %d\n' % (mode, direction, simulated, self.active_low)).encode('ascii'))
            response = b''
            deadline = time.monotonic() + .35
            while b'\n' not in response:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('feedback acknowledgement deadline')
                link.settimeout(remaining)
                part = link.recv(128)
                if not part or len(response) > 256:
                    raise ValueError('invalid feedback acknowledgement')
                response += part
        if not response.startswith(('LED1 OK %08x ' % command).encode('ascii')):
            raise ValueError('FPGA did not acknowledge the feedback command: ' + repr(response))

    def run(self):
        while not self.stop_event.is_set():
            with self.lock:
                state, updated = self.latest, self.updated
            now = time.monotonic()
            if now - updated > .75:
                state = (UNKNOWN, 0, False, 'preview_not_updating')
            state = self.policy.update(state, now)
            try:
                self.send(state)
                report = state[0], state[1], state[2], state[3]
                if report != self.last_report:
                    print('FPGA_LED_APPLIED mode=%s direction=%d simulated=%d reason=%s' % (
                        NAMES[state[0]], state[1], state[2], state[3]), flush=True)
                self.last_report = report
            except (OSError, ValueError) as error:
                if self.last_report != 'disconnected':
                    print('FPGA_LED_DISCONNECTED ' + str(error), flush=True)
                self.last_report = 'disconnected'
            self.stop_event.wait(.2)
        try:
            self.send((OFF, 0, False, 'preview_closed'))
        except (OSError, ValueError): pass

    def stop(self):
        self.stop_event.set()
