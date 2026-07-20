"""Aciklanabilir, deterministik sentetik simulasyon motoru (heuristic baseline).

Bu paket gercek insan davranisi uretmez; kalibrasyon verisi yoktur
(`calibration_status=uncalibrated`, bkz. `app.models.simulations.CalibrationStatus`).
Tum sonuclar "sentetik senaryo tahmini"dir (bkz. docs/scientific-integrity.md
ve docs/methodology.md). Bu paket Playwright, bir LLM/AI saglayicisi, Kafka
veya ChromaDB kullanmaz (bkz. README "Kapsam disi").
"""
