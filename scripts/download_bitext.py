#!/usr/bin/env python3
"""Bitext Customer Support 데이터셋 다운로드 → data/raw/.

CDLA-Sharing-1.0(share-alike) 재배포 조건을 피하기 위해 원본을 커밋하지 않고
이 스크립트로 재현한다(DESIGN.md 4.5절). 이미 존재하면 재다운로드하지 않는다.
"""
import pathlib

import httpx

DATASET_URL = (
    "https://huggingface.co/datasets/bitext/"
    "Bitext-customer-support-llm-chatbot-training-dataset/resolve/main/"
    "Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"
)
OUTPUT_PATH = pathlib.Path("data/raw/bitext_customer_support.csv")


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT_PATH.exists():
        print(f"이미 존재함: {OUTPUT_PATH} (재다운로드 생략)")
        return

    print(f"다운로드 중: {DATASET_URL}")
    with httpx.stream("GET", DATASET_URL, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with open(OUTPUT_PATH, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)

    print(f"완료: {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
