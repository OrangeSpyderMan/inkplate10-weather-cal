LOCAL_ARDUINO_CLI := .tools/arduino-cli
ARDUINO_CLI ?= $(if $(wildcard $(LOCAL_ARDUINO_CLI)),$(LOCAL_ARDUINO_CLI),arduino-cli)
FIRMWARE_SKETCH ?= src
FIRMWARE_BUILD_DIR ?= build/arduino
FIRMWARE_SKETCHBOOK_DIR ?= build/sketchbook
export ARDUINO_DIRECTORIES_USER := $(CURDIR)/$(FIRMWARE_SKETCHBOOK_DIR)
FIRMWARE_FQBN ?= Inkplate_Boards:esp32:Inkplate10V2
FIRMWARE_CORE ?= Inkplate_Boards:esp32@8.1.0
FIRMWARE_BOARD_URL ?= https://github.com/SolderedElectronics/Dasduino-Board-Definitions-for-Arduino-IDE/raw/master/package_Dasduino_Boards_index.json
FIRMWARE_LIBRARIES ?= InkplateLibrary ArduinoJson MQTTLogger Queue StreamUtils YAMLDuino ezTime SdFat
FIRMWARE_UPLOAD_SPEED ?= 115200

.PHONY: firmware-install-cli firmware-setup firmware-compile firmware-upload firmware-clean firmware-board-list

firmware-install-cli:
	bin/install_arduino_cli.sh

firmware-setup:
	$(ARDUINO_CLI) core update-index --additional-urls $(FIRMWARE_BOARD_URL)
	$(ARDUINO_CLI) core install $(FIRMWARE_CORE) --additional-urls $(FIRMWARE_BOARD_URL)
	@for lib in $(FIRMWARE_LIBRARIES); do \
		$(ARDUINO_CLI) lib install "$$lib"; \
	done

firmware-compile:
	$(ARDUINO_CLI) compile \
		--fqbn $(FIRMWARE_FQBN) \
		--build-path $(FIRMWARE_BUILD_DIR) \
		$(FIRMWARE_SKETCH)

firmware-upload:
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
	rm -rf $(FIRMWARE_BUILD_DIR) $(FIRMWARE_SKETCHBOOK_DIR)

firmware-board-list:
	$(ARDUINO_CLI) board list
