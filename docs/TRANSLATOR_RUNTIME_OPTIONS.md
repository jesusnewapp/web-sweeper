# Translator runtime and download options

Web Sweeper contains a vendor-neutral translation bridge and Translator Sweeper
orchestration. It does **not** bundle translation model weights. Keeping the
runtime separate makes the core small, lets operators choose licenses and
hardware deliberately, and prevents a model download from silently consuming
many gigabytes.

## Before downloading

1. Check free disk, memory, CPU/GPU support, and the model's license.
2. Start with one required language pair; do not install every model by default.
3. Preserve the original work and its SHA-256 hash.
4. Treat machine translation as a derived work.
5. Require independent target-language validation before translation staging.
6. Use the shared overall uploader for live promotion; the Translator Sweeper
   must never create a second production writer.

Package and model sizes vary by version and language. Measure the actual
download and extracted working set before enabling a batch. Sweeper should
retain enough free space for the model, translated output, atomic temporary
files, staging evidence, and recovery.

## Option 1: Argos Translate (simplest offline start)

[Argos Translate](https://github.com/argosopentech/argos-translate) is an
open-source offline translation library with command-line support and separate
`.argosmodel` packages for language pairs. Its package index lets an operator
install only the pairs needed. Indirect translation through an intermediate
language is possible, but may reduce quality and should be recorded and reviewed.

```bash
python3 -m venv .venv-translate
source .venv-translate/bin/activate
python -m pip install --upgrade pip
python -m pip install argostranslate
argospm update
argospm search
argospm install translate-en_es
argos-translate --from en --to es "Test sentence"
```

Official resources:

- [Repository and installation](https://github.com/argosopentech/argos-translate)
- [Python and package documentation](https://argos-translate.readthedocs.io/en/stable/source/argostranslate.html)

This is the most approachable local option, but it is not automatically
publication-quality for long books. Validate terminology, omissions,
hallucinated additions, names, quotations, notes, and structural preservation.

## Option 2: CTranslate2 with a compatible model

[CTranslate2](https://opennmt.net/CTranslate2/) is an efficient inference
runtime. It is not itself a translator: a compatible converted model and
tokenizer are still required.

```bash
python3 -m venv .venv-translate
source .venv-translate/bin/activate
python -m pip install --upgrade pip
python -m pip install ctranslate2 sentencepiece
```

Official resource:

- [CTranslate2 installation](https://opennmt.net/CTranslate2/installation.html)

This is a strong choice for a controlled production adapter when the operator
has selected and tested a compatible model for the required language pair.

## Option 3: MarianMT / OPUS models

MarianMT models are available through Hugging Face Transformers, including
models published by the University of Helsinki's language-technology group.
This option usually has a heavier Python runtime than Argos and downloads model
weights separately.

```bash
python3 -m venv .venv-translate
source .venv-translate/bin/activate
python -m pip install --upgrade pip
python -m pip install transformers sentencepiece torch
```

Official resource:

- [MarianMT documentation](https://huggingface.co/docs/transformers/main/model_doc/marian)

Inspect the exact model card and license before downloading or publishing its
output. Do not assume all models on a model hub have the same terms.

## Option 4: NLLB-200 (broad coverage, heavier and license-limited)

Meta's NLLB family offers broad multilingual coverage. The commonly referenced
distilled 600M model is published under `CC-BY-NC-4.0`, so it is not a universal
drop-in choice for commercial or unrestricted deployments.

- [NLLB-200 overview](https://ai.meta.com/blog/nllb-200-high-quality-machine-translation/)
- [NLLB-200 distilled 600M model card and license](https://huggingface.co/facebook/nllb-200-distilled-600M)

Use it only when its license fits the project, adequate storage is available,
and the target-language validator has been tested against representative books.

## Option 5: LibreTranslate or another hosted adapter

[LibreTranslate](https://docs.libretranslate.com/) provides a self-hostable API
powered by Argos Translate. A hosted or institutional translation service can
also be used through Sweeper's command adapter. This avoids storing every model
on the Sweeper host, but introduces network, privacy, cost, retention, and
provider-policy considerations.

Never send confidential or restricted text to a remote service without explicit
authorization. Record provider, model/version when available, request time, and
the hashes of the source and returned translation.

## Sweeper command adapter contract

Configure one command per language direction:

```bash
export SWEEPER_TRANSLATOR_EN_ES=/absolute/path/to/translator-adapter
```

Sweeper sends one JSON object to the command's standard input:

```json
{
  "source": "en",
  "target": "es",
  "text": "Complete source text",
  "source_sha256": "..."
}
```

The adapter must exit successfully and return only:

```json
{"translation": "Complete translated text"}
```

The bridge preserves the original, rejects empty output and extreme length
ratios, hashes the translated bytes, and writes translation evidence. Those are
technical checks, not linguistic approval.

## Translator Sweeper staging contract

Public Web Sweeper keeps Firebase or another database behind an explicit adapter.
Configure a separate translation staging collection and independent commands:

The generated `REPLACE_WITH_YOUR_TRANSLATION_STAGING_COLLECTION` value is a
placeholder, not a real Firebase collection. Translation cannot be enabled
until it is replaced. The public package contains no Codex project ID,
credentials, private collection names, or hardcoded live destination.

```json
"translation": {
  "enabled": true,
  "batch_size": 50,
  "staging_collection": "sweeper_translation_staging",
  "target_languages": ["es"],
  "notifier_command": ["/absolute/path/to/notify-translator"],
  "validator_command": ["/absolute/path/to/validate-spanish"],
  "stager_command": ["/absolute/path/to/stage-translations"]
}
```

The validator must confirm the target language and the exact translated
SHA-256. The staging adapter must confirm the exact validated membership. Once
that confirmation is durable, the Translator Sweeper may queue its next batch.
It writes a live-handoff artifact for the shared overall uploader; it does not
publish concurrently or bypass live verification.

## Recommended adoption order

1. Free enough disk for the chosen runtime, one model pair, and recovery margin.
2. Install one offline pair, usually Argos for the first integration test.
3. Translate a small representative evaluation set.
4. Test the independent target-language validator and rejection path.
5. Test separate translation staging with a no-live-write adapter.
6. Rehearse the shared uploader handoff and exact deployment verification.
7. Increase batch size only after measuring quality, throughput, storage, and
   recovery behavior. The public item ceiling is 1,000 per batch.
