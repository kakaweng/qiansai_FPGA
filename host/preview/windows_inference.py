import warnings


class DirectMLNetwork:
    def __init__(self, path, threads):
        import onnxruntime
        import numpy
        if "DmlExecutionProvider" not in onnxruntime.get_available_providers():
            raise RuntimeError("DirectML provider is unavailable; install onnxruntime-directml")
        options = onnxruntime.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = threads
        self.session = onnxruntime.InferenceSession(str(path), sess_options=options,
                                                    providers=[("DmlExecutionProvider", {"device_id": 0}), "CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        input_type = self.session.get_inputs()[0].type
        if input_type not in ("tensor(float)", "tensor(float16)"):
            raise ValueError(f"Unsupported ONNX input type: {input_type}")
        self.input_dtype = numpy.float16 if input_type == "tensor(float16)" else numpy.float32
        self.output_names = tuple(output.name for output in self.session.get_outputs())
        self.blob = None
        self.runtime = {"backend": "onnxruntime-directml", "providers": self.session.get_providers(),
                        "version": onnxruntime.__version__, "input_type": input_type, "device_id": 0}

    def setInput(self, blob):
        self.blob = blob.astype(self.input_dtype, copy=False)

    def getUnconnectedOutLayersNames(self):
        return self.output_names

    def forward(self, names):
        return self.session.run(list(names), {self.input_name: self.blob})


def load_inference_network(path, threads=4, backend="auto"):
    import cv2
    import numpy
    cv2.setNumThreads(threads)
    if backend not in ("auto", "cpu", "directml"):
        raise ValueError("backend must be auto, cpu or directml")
    if backend != "cpu":
        try:
            network = DirectMLNetwork(path, threads)
            network.setInput(numpy.zeros((1, 3, 640, 640), dtype=numpy.float32))
            network.forward(network.getUnconnectedOutLayersNames())
            return network, network.runtime
        except Exception as error:
            if backend == "directml":
                raise RuntimeError(f"DirectML initialization failed: {error}") from error
            warnings.warn(f"GPU inference unavailable; using OpenCV CPU: {error}", RuntimeWarning)
    network = cv2.dnn.readNetFromONNX(str(path))
    return network, {"backend": "opencv-cpu", "providers": ["CPU"], "version": cv2.__version__}
