"""
视频/音频语音转文字工具（免费、本地离线，音频不上传）

用法：
    python transcribe_audio.py <媒体文件路径> [--model small|medium|large-v3] [--lang zh]

说明：
    - 依赖 faster-whisper，必须在 .asr-env 虚拟环境中运行（Python 3.10）：
        C:/Users/wr/.asr-env/Scripts/python.exe
    - ctranslate2 必须固定为 4.4.0（4.9.0 在本机加载模型会段错误）
    - 模型缓存在 C:/Users/wr/.cache/huggingface，首次使用需通过 hf-mirror.com 下载
    - 支持 mp4/mov/avi 等视频与 mp3/wav/m4a 等音频（由 PyAV 解码，无需 ffmpeg）

输出：
    默认在控制台打印带时间戳的文字；加 --out <路径> 可同时保存到 txt 文件。
"""

import argparse
import os
import sys

# 必须在 huggingface_hub 导入前设置，使用国内镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from faster_whisper import WhisperModel

# 本机已下载的模型缓存路径（避免每次联网校验）
MODEL_CACHE = {
    "small": r"C:\Users\wr\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots\536b0662742c02347bc0e980a01041f333bce120",
}


def main():
    parser = argparse.ArgumentParser(description="本地语音转文字")
    parser.add_argument("media", help="视频或音频文件路径")
    parser.add_argument("--model", default="small", help="模型规格，默认 small")
    parser.add_argument("--lang", default="zh", help="语言代码，默认 zh（中文）")
    parser.add_argument("--out", default=None, help="可选：结果保存的 txt 路径")
    args = parser.parse_args()

    if not os.path.isfile(args.media):
        print(f"文件不存在: {args.media}", file=sys.stderr)
        sys.exit(1)

    model_path = MODEL_CACHE.get(args.model, args.model)
    print(f"加载模型 ({args.model})...", file=sys.stderr)
    model = WhisperModel(model_path, device="cpu", compute_type="int8")
    print("开始转写...", file=sys.stderr)

    segments, info = model.transcribe(
        args.media,
        language=args.lang if args.lang else None,
        beam_size=5,
    )

    lines = []
    print(f"检测语言: {info.language}  时长: {info.duration:.1f}s", file=sys.stderr)
    print("-" * 60)
    for seg in segments:
        line = f"[{seg.start:7.2f} - {seg.end:7.2f}] {seg.text.strip()}"
        print(line)
        lines.append(line)
    print("-" * 60)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"结果已保存: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
