.PHONY: up up-voz down test demo bench local-start local-stop
up:            ; docker compose up --build -d
# Uso con la tablet (voz real). `up` y los experimentos dejan STT en echo a propósito.
# small + vocabulario kichwa: con base, «yaku» sale «y aco»; small lo reconoce (~0,8 s por palabra en CPU).
signs-gen:     ; .venv/bin/python scripts/generate_signs.py --apply    # señas provisionales (ver el script)
signs-gen-rm:  ; .venv/bin/python scripts/generate_signs.py --remove   # quitarlas antes de evaluar
up-voz:        ; WHISPER_MODEL=small KICHWA_HOTWORDS_PATH=/srv/kichwa_dictionary.json docker compose -f docker-compose.yml -f docker-compose.whisper.yml up --build -d
down:          ; docker compose down -v
test:          ; python -m pytest services -q
demo:          ; python scripts/e2e_demo.py "hola mamá"
bench:         ; python scripts/benchmark_latency.py --n 30 --out results/bench.csv
local-start:   ; scripts/run_local.sh start
local-stop:    ; scripts/run_local.sh stop
migrate-signs: ; python scripts/migrate_excel_signs.py ../robot_conection_app/assets/abecedario.xlsx data/signs_seed.json
