LOCAL_ARDUINO_CLI := .tools/arduino-cli
ARDUINO_CLI ?= $(if $(wildcard $(LOCAL_ARDUINO_CLI)),$(LOCAL_ARDUINO_CLI),arduino-cli)
FIRMWARE_SKETCH ?= src
FIRMWARE_SKETCHBOOK_DIR ?= build/sketchbook
export ARDUINO_DIRECTORIES_USER := $(CURDIR)/$(FIRMWARE_SKETCHBOOK_DIR)
FIRMWARE_FQBN ?= Inkplate_Boards:esp32:Inkplate10V2
FIRMWARE_CORE ?= Inkplate_Boards:esp32@8.1.0
FIRMWARE_CORE_ID := $(firstword $(subst @, ,$(FIRMWARE_CORE)))
FIRMWARE_BOARD_URL ?= https://github.com/SolderedElectronics/Dasduino-Board-Definitions-for-Arduino-IDE/raw/master/package_Dasduino_Boards_index.json
FIRMWARE_LIBRARIES ?= InkplateLibrary ArduinoJson Queue StreamUtils YAMLDuino ezTime SdFat
FIRMWARE_UPLOAD_SPEED ?= 115200
FIRMWARE_VERSION ?=
FIRMWARE_VERSION_DIR := build/firmware-version
FIRMWARE_VERSION_HEADER := $(FIRMWARE_VERSION_DIR)/firmware_version.h
FIRMWARE_COMMON_BUILD_FLAGS := -I$(abspath $(FIRMWARE_VERSION_DIR))

ifeq ($(strip $(CONFIG)),)
FIRMWARE_CONFIG_MODE := sd
FIRMWARE_BUILD_DIR ?= build/arduino-sd
FIRMWARE_CONFIG_DEPS :=
FIRMWARE_MODE_BUILD_FLAGS :=
else
FIRMWARE_CONFIG_MODE := embedded
FIRMWARE_BUILD_DIR ?= build/arduino-embedded
FIRMWARE_GENERATED_DIR := build/firmware-config
FIRMWARE_GENERATED_HEADER := $(FIRMWARE_GENERATED_DIR)/embedded_config.h
FIRMWARE_CONFIG_DEPS := firmware-generate-config
FIRMWARE_MODE_BUILD_FLAGS := -DEMBEDDED_CONFIG -I$(abspath $(FIRMWARE_GENERATED_DIR))
endif

FIRMWARE_BUILD_PROPERTIES := --build-property "compiler.cpp.extra_flags=$(FIRMWARE_COMMON_BUILD_FLAGS) $(FIRMWARE_MODE_BUILD_FLAGS)"

.PHONY: world version-manifest firmware-world firmware-ensure-cli firmware-ensure-setup firmware-install-cli firmware-setup firmware-generate-version firmware-generate-config firmware-compile firmware-upload firmware-clean firmware-distclean firmware-board-list

world: firmware-world

version-manifest:
	python3 bin/generate_version_manifest.py $(if $(FIRMWARE_VERSION),--version "$(FIRMWARE_VERSION)")

firmware-world:
	$(MAKE) firmware-ensure-cli
	$(MAKE) firmware-ensure-setup
	$(MAKE) firmware-compile

firmware-ensure-cli:
	@if command -v "$(ARDUINO_CLI)" >/dev/null 2>&1; then \
		$(ARDUINO_CLI) version; \
	else \
		$(MAKE) firmware-install-cli; \
	fi

firmware-ensure-setup:
	@missing_core=0; \
	if ! { $(ARDUINO_CLI) core list | awk '$$1 == "$(FIRMWARE_CORE_ID)" { found = 1 } END { exit !found }'; }; then \
		missing_core=1; \
	fi; \
	missing_libs=""; \
	for lib in $(FIRMWARE_LIBRARIES); do \
		if ! { $(ARDUINO_CLI) lib list "$$lib" | awk -v lib="$$lib" '$$1 == lib { found = 1 } END { exit !found }'; }; then \
			missing_libs="$$missing_libs $$lib"; \
		fi; \
	done; \
	if [ "$$missing_core" -eq 1 ] || [ -n "$$missing_libs" ]; then \
		if [ "$$missing_core" -eq 1 ]; then \
			echo "Missing firmware platform: $(FIRMWARE_CORE)"; \
		fi; \
		if [ -n "$$missing_libs" ]; then \
			echo "Missing firmware libraries:$$missing_libs"; \
		fi; \
		$(MAKE) firmware-setup; \
	else \
		echo "Firmware platform and libraries are already installed."; \
	fi

firmware-install-cli:
	bash bin/install_arduino_cli.sh

firmware-setup:
	$(ARDUINO_CLI) core update-index --additional-urls $(FIRMWARE_BOARD_URL)
	$(ARDUINO_CLI) core install $(FIRMWARE_CORE) --additional-urls $(FIRMWARE_BOARD_URL)
	@for lib in $(FIRMWARE_LIBRARIES); do \
		$(ARDUINO_CLI) lib install "$$lib"; \
	done

firmware-generate-version: version-manifest
	python3 bin/generate_firmware_version.py "$(FIRMWARE_VERSION_HEADER)" "$(FIRMWARE_VERSION)"

ifneq ($(strip $(CONFIG)),)
firmware-generate-config:
	python3 bin/generate_firmware_config.py "$(CONFIG)" "$(FIRMWARE_GENERATED_HEADER)"
endif

firmware-compile: firmware-generate-version $(FIRMWARE_CONFIG_DEPS)
	@echo "Compiling $(FIRMWARE_CONFIG_MODE) firmware"
	$(ARDUINO_CLI) compile \
		--fqbn $(FIRMWARE_FQBN) \
		--build-path $(FIRMWARE_BUILD_DIR) \
		$(FIRMWARE_BUILD_PROPERTIES) \
		$(FIRMWARE_SKETCH)

firmware-upload: firmware-compile
ifndef PORT
	$(error PORT is required, for example: make firmware-upload PORT=/dev/ttyUSB0)
endif
	$(ARDUINO_CLI) upload \
		--fqbn $(FIRMWARE_FQBN) \
		--port $(PORT) \
		--input-dir $(FIRMWARE_BUILD_DIR) \
		--upload-property upload.speed=$(FIRMWARE_UPLOAD_SPEED) \
		$(FIRMWARE_SKETCH)

firmware-clean:
	rm -rf build/arduino-sd build/arduino-embedded build/firmware-config $(FIRMWARE_VERSION_DIR)

firmware-distclean: firmware-clean
	rm -rf $(FIRMWARE_SKETCHBOOK_DIR)

firmware-board-list:
	$(ARDUINO_CLI) board list
