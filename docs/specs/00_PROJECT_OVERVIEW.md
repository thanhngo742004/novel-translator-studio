# 00 — Project Overview

## Product name

Novel Translator Studio

## Product goal

Build a local-first CLI + future desktop app for translating text novels and manga/comics into Vietnamese.

The system learns from:

- raw source text
- human translations
- AI translations
- human corrections
- glossary/name/pronoun/style rules
- manga box/OCR/layout corrections later

The app manages full LAMM-T memory. Plugins such as VBook only receive compact exported memory and do not self-learn.

## Primary interfaces

1. CLI `nts` — canonical automation interface.
2. Future GUI — thin orchestration layer over the same application services.
3. Future OpenClaw integration — calls CLI with JSON I/O.
4. Future plugin export — compact read-only bundle for VBook or similar clients.

## MVP focus

MVP starts with CLI and text translation memory foundation.

MVP0 is only skeleton/config/storage/task tracking/mock provider.

Do not start with manga automation or GUI.
