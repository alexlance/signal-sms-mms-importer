#!/bin/bash
set -euxo pipefail

trap 'rm -rfv bits; exit' EXIT INT ERR

# ensure 30 digit key is set
test -v SIG_KEY

# ensure filename of Signal backup file is set
test -v SIG_FILE
test -e ${SIG_FILE}

# ensure filename of Backup and Restore XML file is set
test -v BAR_FILE
test -e ${BAR_FILE}

# signalbackup-tools can dump it's artifacts into the bits sub-folder
mkdir -p bits
rm -f signal-all-messages.backup

# extract the signal backup to bits/
signalbackup-tools ${SIG_FILE} ${SIG_KEY} --output bits/

# take all the messages out of the XML file and replay them into the Signal sqlite file
python3 sms-mms-import-to-signal.py ${BAR_FILE} bits/

# re-wrap up the signal backup file into signal-all-messages.backup
signalbackup-tools bits/ --output signal-all-messages.backup --opassword ${SIG_KEY}

echo "Wrote: signal-all-messages.backup"
echo "Now transfer it to your phone an import it into a fresh Signal install"
