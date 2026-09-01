from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path


LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ru": "Russian",
    "el": "Greek", "la": "Latin", "pl": "Polish", "ja": "Japanese",
    "zh": "Chinese", "cs": "Czech", "sv": "Swedish", "da": "Danish",
    "no": "Norwegian", "fi": "Finnish", "hu": "Hungarian",
    "ro": "Romanian", "uk": "Ukrainian", "ar": "Arabic", "he": "Hebrew",
    "tr": "Turkish", "ko": "Korean", "bg": "Bulgarian", "hr": "Croatian",
    "sr": "Serbian", "sk": "Slovak", "sl": "Slovenian", "ca": "Catalan",
    "eu": "Basque", "gl": "Galician", "is": "Icelandic",
}


def engine_variable(source: str, target: str) -> str:
    return f"SWEEPER_TRANSLATOR_{source.upper()}_{target.upper()}"


def capabilities() -> dict:
    pairs = []
    for source in LANGUAGES:
        for target in LANGUAGES:
            if source == target:
                continue
            variable = engine_variable(source, target)
            command = os.environ.get(variable, "").strip()
            pairs.append({"source": source, "target": target, "environment": variable,
                          "available": bool(command), "command": command or None})
    return {"languages": LANGUAGES, "pairs": pairs,
            "available_pairs": sum(1 for row in pairs if row["available"])}


def translate_file(input_path: Path, output_path: Path, source: str, target: str,
                   command: str | None = None) -> dict:
    if source not in LANGUAGES or target not in LANGUAGES or source == target:
        raise ValueError("source and target must be different supported language codes")
    engine = command or os.environ.get(engine_variable(source, target), "").strip()
    if not engine:
        raise ValueError(f"no engine configured; set {engine_variable(source, target)}")
    source_bytes = input_path.read_bytes()
    request = {"source": source, "target": target,
               "text": source_bytes.decode("utf-8"), "source_sha256": hashlib.sha256(source_bytes).hexdigest()}
    result = subprocess.run(shlex.split(engine), input=json.dumps(request), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ValueError(f"translation engine failed: {result.stderr[-500:]}")
    response = json.loads(result.stdout)
    translated = str(response.get("translation", ""))
    if not translated.strip():
        raise ValueError("translation engine returned empty output")
    ratio = len(translated) / max(1, len(request["text"]))
    if not 0.2 <= ratio <= 5.0:
        raise ValueError(f"unsafe translation length ratio: {ratio:.3f}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(translated, encoding="utf-8")
    temporary.replace(output_path)
    evidence = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "source_language": source, "target_language": target,
        "source_path": str(input_path), "output_path": str(output_path),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "translation_sha256": hashlib.sha256(translated.encode()).hexdigest(),
        "length_ratio": ratio, "derived_output": True,
        "original_overwritten": False, "human_or_domain_validation_required": True}
    evidence_path = output_path.with_suffix(output_path.suffix + ".translation.json")
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence
