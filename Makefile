# Jedno wejście do wszystkich sprawdzeń.
#
# `make` bez argumentu wypisuje listę celów. Każdy cel to nazwa + komendy,
# które make odpala po kolei. `make sprawdz` zatrzymuje się na PIERWSZYM
# niepowodzeniu — nie leci dalej, żeby nie zasypywać cię błędami wtórnymi.
#
# Uwaga przy edycji: linie z komendami muszą zaczynać się TABULATOREM.
# Spacje dają błąd "missing separator". Pilnuje tego .editorconfig.

UV := uv run --frozen

.DEFAULT_GOAL := pomoc
.PHONY: pomoc instalacja lint format typy testy sprawdz hooki

pomoc:  ## wypisz dostępne cele
	@awk 'BEGIN{FS=":.*## "} /^[a-z]+:.*## / {printf "  make %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

instalacja:  ## zsynchronizuj środowisko i włącz bramkę pre-commit
	uv sync
	$(UV) pre-commit install

lint:  ## ruff check (nie zapisuje zmian)
	$(UV) ruff check .

format:  ## ruff format (ZAPISUJE zmiany)
	$(UV) ruff format .

typy:  ## mypy
	$(UV) mypy

testy:  ## pytest
	$(UV) pytest

sprawdz: lint typy testy  ## pełna bramka — lint, typy, testy
	@echo "OK — wszystkie sprawdzenia przeszły"

hooki:  ## odpal wszystkie hooki pre-commit na całym repo
	$(UV) pre-commit run --all-files
