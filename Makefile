LOCAL_ARDUINO_CLI := .tools/arduino-cli
ARDUINO_CLI ?= $(if $(wildcard $(LOCAL_ARDUINO_CLI)),$(LOCAL_ARDUINO_CLI),arduino-cli)
FIRMWARE_SKETCH ?= src
FIRMWARE_SKETCHBOOK_DIR ?= build/sketchbook
export ARDUINO_DIRECTORIES_USER := $(CURDIR)/$(FIRMWARE_SKETCHBOOK_DIR)
FIRMWARE_FQBN ?= Inkplate_Boards:esp32:Inkplate10V2
FIRMWARE_CORE ?= Inkplate_Boards:esp32@8.1.0
FIRMWARE_BOARD_URL ?= https://github.com/SolderedElectronics/Dasduino-Board-Definitions-for-Arduino-IDE/raw/master/package_Dasduino_Boards_index.json
FIRMWARE_LIBRARIES ?= InkplateLibrary ArduinoJson MQTTLogger Queue StreamUtils YAMLDuino ezTime SdFat
FIRMWARE_UPLOAD_SPEED ?= 115200

ifeq ($(strip $(CONFIG)),)
FIRMWARE_CONFIG_MODE := sd
FIRMWARE_BUILD_DIR ?= build/arduino-sd
FIRMWARE_CONFIG_DEPS :=
FIRMWARE_BUILD_PROPERTIES :=
else
FIRMWARE_CONFIG_MODE := embedded
FIRMWARE_BUILD_DIR ?= build/arduino-embedded
FIRMWARE_GENERATED_DIR := build/firmware-config
FIRMWARE_GENERATED_HEADER := $(FIRMWARE_GENERATED_DIR)/embedded_config.h
FIRMWARE_CONFIG_DEPS := firmware-generate-config
FIRMWARE_BUILD_PROPERTIES := --build-property "compiler.cpp.extra_flags=-DEMBEDDED_CONFIG -I$(abspath $(FIRMWARE_GENERATED_DIR))"
endif

.PHONY: firmware-install-cli firmware-setup firmware-generate-config firmware-compile firmware-upload firmware-clean firmware-board-list

firmware-install-cli:
	bash bin/install_arduino_cli.sh

firmware-setup:
	$(ARDUINO_CLI) core update-index --additional-urls $(FIRMWARE_BOARD_URL)
	$(ARDUINO_CLI) core install $(FIRMWARE_CORE) --additional-urls $(FIRMWARE_BOARD_URL)
	@for lib in $(FIRMWARE_LIBRARIES); do \
		$(ARDUINO_CLI) lib install "$$lib"; \
	done

ifneq ($(strip $(CONFIG)),)
firmware-generate-config:
	python3 bin/generate_firmware_config.py "$(CONFIG)" "$(FIRMWARE_GENERATED_HEADER)"
endif

firmware-compile: $(FIRMWARE_CONFIG_DEPS)
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
	rm -rf build/arduino-sd build/arduino-embedded build/firmware-config $(FIRMWARE_SKETCHBOOK_DIR)

firmware-board-list:
	$(ARDUINO_CLI) board list
