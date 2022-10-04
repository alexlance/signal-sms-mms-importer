
DOCKER := docker run -e SIG_KEY -e SIG_FILE -e BAR_FILE -it -v $${PWD}:/root/ workspace


run:
	# Set these env vars, eg:
	@test -n "${SIG_KEY}"  || { echo "No SIG_KEY set, try eg: export SIG_KEY=123456789101112131415161718192"; exit 1; } || true
	@test -n "${SIG_FILE}" || { echo "No SIG_FILE set, try eg: export SIG_FILE=signal-2022-01-01-01-01-01.backup"; exit 1; } || true
	@test -n "${BAR_FILE}" || { echo "No BAR_FILE set, try eg: export BAR_FILE=sms-20220000000000.xml"; exit 1; } || true

	docker build -t workspace .

	rm -rf bits signalsmsmmsimport.log signal-all-messages.backup
	mkdir -p bits
	@echo "Extracting backup file: $${SIG_FILE} to the bits/ folder"
	$(DOCKER) signalbackup-tools $${SIG_FILE} $${SIG_KEY} --output bits/

	@echo "Importing message in the XML file: $${BAR_FILE} into the database in the bits/ folder"
	$(DOCKER) python3 ./sms-mms-import-to-signal.py --input $${BAR_FILE} --output bits/ -m -v

	@echo "Wrapping up the signal database into a new backup file that can be restored: signal-all-messages.backup"
	$(DOCKER) signalbackup-tools bits/ --output signal-all-messages.backup --opassword $${SIG_KEY}

	@echo "Wrote: signal-all-messages.backup"
	rm -rf bits
