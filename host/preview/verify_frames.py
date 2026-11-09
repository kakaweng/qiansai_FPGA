import argparse
from pathlib import Path

from frame_to_png import frame_checksum, read_frame, write_png


def verify_frames(directory, expected_count, png_directory):
    if expected_count < 1:
        raise ValueError("预期帧数必须大于零")
    actual_names = sorted(path.name for path in directory.glob("frame_*")
                          if path.suffix in (".pgm", ".rgb565"))
    suffix = Path(actual_names[0]).suffix if actual_names else ".pgm"
    expected_names = [f"frame_{index:06d}{suffix}" for index in range(1, expected_count + 1)]
    if actual_names != expected_names:
        missing = sorted(set(expected_names) - set(actual_names))
        unexpected = sorted(set(actual_names) - set(expected_names))
        raise ValueError(
            f"帧文件列表不匹配：预期 {expected_count} 个，实际 {len(actual_names)} 个；"
            f"缺少 {missing[:5]}，额外 {unexpected[:5]}"
        )

    if png_directory is not None:
        png_directory.mkdir(parents=True, exist_ok=True)
    checksums = set()
    for name in expected_names:
        try:
            frame = read_frame(directory / name)
        except (OSError, ValueError) as error:
            raise ValueError(f"{name}: {error}") from error
        checksum = frame_checksum(frame)
        checksums.add(checksum)
        print(f"{name}: bytes={len(frame)} checksum=0x{checksum:08x}")
        if png_directory is not None:
            write_png(frame, png_directory / Path(name).with_suffix(".png"))

    print(f"文件检查通过：frames={expected_count}, distinct_checksums={len(checksums)}")
    if expected_count > 1 and len(checksums) == 1:
        print("所有帧的校验值相同；静态输入允许相同，变化中的输入需要进一步检查画面更新。")
    if png_directory is not None:
        print(f"PNG 目录：{png_directory}")


def main():
    parser = argparse.ArgumentParser(description="检查重复采集的帧文件数量、编号和像素大小，可批量转换 PNG")
    parser.add_argument("directory", type=Path, help="包含 frame_000001.pgm 等文件的目录")
    parser.add_argument("--expected", type=int, default=30, help="预期帧数，默认 30")
    parser.add_argument("--png-dir", type=Path, help="可选的 PNG 输出目录")
    arguments = parser.parse_args()
    try:
        verify_frames(arguments.directory, arguments.expected, arguments.png_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, f"检查失败：{error}\n")


if __name__ == "__main__":
    main()
