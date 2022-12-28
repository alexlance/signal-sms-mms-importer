import xml.etree.ElementTree as ET
import os
import sys
import sqlite3
import base64
import time
import logging
import argparse
import pkg_resources

cur_version = pkg_resources.parse_version(str(sqlite3.sqlite_version))
min_version = pkg_resources.parse_version("3.35")

# the RETURNING syntax needs sqlite3 version >=3.35
if cur_version < min_version:
    print(f"Version of sqlite3 python library is too old, upgrade to {min_version}")
    print("Try the Docker version for a predictable build environment")
    sys.exit(1)

parser = argparse.ArgumentParser(
    description='imports SMS and MMS messages in to a Signal Backup')

parser.add_argument('args', nargs='*')
parser.add_argument('--input', '-i', help='input sms backup xml file')
parser.add_argument('--output', '-o', help='exported signal backup to update')
parser.add_argument('--merge', '-m', dest='merge', action='store_true', help="optional argument, to delete any instances of the same sms/mms prior to inserting. useful if this isn't your first time")
parser.add_argument('--verbose', '-v', dest='verbose', action='store_true', help='turns logging from info/warning only, to every debug item')

args = parser.parse_args()

input = args.input if args.input is not None else args.args[0]
output = args.output if args.output is not None else args.args[1]

logging.basicConfig(filename='signalsmsmmsimport.log', filemode='a', format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=logging.DEBUG if args.verbose else logging.INFO)

logging.info(f"input file is '{input}', output file is '{output}'")


def get_contacts(cursor):
    cursor.execute("select _id, phone, system_display_name from recipient")
    contacts = cursor.fetchall()
    contacts_by_number = {}
    for c in contacts:
        if c[1]:
            contacts_by_number[c[1]] = c[0]
            contacts_by_number[c[1].replace("+61", "0")] = c[0]
            contacts_by_number[c[1].replace("+61", "0").replace("-", "")] = c[0]
    return contacts_by_number


def get_groups(cursor):
    cursor.execute("select _id, group_id, recipient_id, members from group")
    groups = cursor.fetchall()
    groups_by_number = {}
    for g in groups:
        groups_by_number['_id'] = g[0]
        groups_by_number['group_id'] = g[1]
        groups_by_number['recipient_id'] = g[2]
        groups_by_number['members'] = g[3]
    return groups_by_number


def get_parts(r):
    rtn = []
    logging.debug("GETTING PARTS:")
    for parts in r.findall("parts"):
        for part in parts.findall("part"):
            rtn.append(part)
    return rtn


def get_addrs(r):
    # this is a list of addresses, type 151 is you, type 130 are other
    # recipients, type 137 is the sender. this will be useful for groups (need
    # to figure how signal marks the sender in a group) also useful
    # this returned list can be used to allocate messages to the sender of a group
    rtn = []
    logging.debug("GETTING ADDRS:")
    for addrs in r.findall("addrs"):
        for addr in addrs.findall("addr"):
            rtn.append(addr)
    return rtn


def add_recipient(add, cursor):
    global contacts_by_number
    cursor.execute(f"""insert into recipient (phone, default_subscription_id, registered) values ("{add}", 1, 2)""")
    conn.commit()
    contacts_by_number = get_contacts(cursor)
    return contacts_by_number[add]


def get_or_make_thread(cursor, r):
    thread_id = False
    cursor.execute(f"select _id from thread where recipient_id = '{r['recipient_id']}'")
    rows = cursor.fetchall()
    if len(rows):
        thread_id = rows[0][0]
    if not thread_id:
        logging.debug(f"Creating new thread: {r}")
        cursor.execute(
            "insert into thread (date, recipient_id, snippet) values (?, ?, ?) returning _id",
            (r['date_sent'], r['recipient_id'], str(r['body'])[0:100]),
        )
        (thread_id, ) = cursor.fetchone()
    return thread_id


# parse the XML file generated by SMS Backup and Restore (by SyncTech)
logging.info('starting parsing xml file')
tree = ET.parse(input)
root = tree.getroot()
logging.info('finished parsing xml file')

# parse the sqlite database generated by github.com/bepaald/signalbackup-tools
conn = sqlite3.connect(os.path.join(output, "database.sqlite"))
cursor = conn.cursor()

smses = []
mmses = []
contacts_by_number = get_contacts(cursor)
# cont[phone] = _id  (recipient_id!)

for r in root:
    date_sent = r.attrib.get("date_sent", "")
    if date_sent in [0, "0", ""] or len(date_sent) < 13:
        date_sent = r.attrib.get("date", "")
    addrs = get_addrs(r)
    address = False
    add = r.attrib["address"].replace("-", "").replace("+61", "0")
    add_list = []
    if '~' in add:
        for a in sorted(add.split("~")):
            try:
                address = contacts_by_number[a]
            except KeyError:
                address = add_recipient(a, cursor)
            finally:
                add_list.append(address)
        else:
            for addr in addrs:
                if addr.items()[1][1] == '137':
                    address = contacts_by_number[addr.items()[0][1].replace("-", "").replace("+61", "0")]
        add_list = [*set(add_list)]
    if not address:
        try:
            address = contacts_by_number[add]
        except KeyError:
            address = add_recipient(add, cursor)
    row = {}
    if r.tag == "sms":
        # sms sent is type 87, sms received is type 20
        row['add_list'] = add_list
        row['recipient_id'] = address
        row['date_sent'] = date_sent
        row['read'] = 1  # "read"
        row['type'] = 87 if str(r.attrib["type"]) == "2" else 20
        row['reply_path_present'] = None if str(r.attrib["type"]) == "2" else 0
        row['body'] = r.attrib["body"]
        row['subscription_id'] = -1 if str(r.attrib["type"]) == "2" else 1
        smses.append(row)
    elif r.tag == "mms":
        # magic mms numbers, 128 for messages we've sent, 132 for messages we've received
        # for msg_box, 87 is sent inc text, 23 is sent no text, 20 is received with or without text
        parts = get_parts(r)
        text = ""
        for text_part in parts:
            if text_part.get("seq") == '0':
                text = text_part.get("text", "")
                if text in ["null", ""]:
                    text = None
                if text:
                    break
        row['add_list'] = add_list
        row['recipient_id'] = address
        row['date_sent'] = date_sent
        row['read'] = 1  # "read"
        row['status'] = -1  # "status",
        row['type'] = 10485783
        row['subscription_id'] = -1
        row['st'] = None
        row['body'] = r.attrib.get("body", text)
        row['parts'] = parts
        row['addrs'] = addrs
        if len(row['add_list']) and row['type'] == 128:
            for add in row['add_list']:
                row['recipient_id'] = add
                mmses.append(row.copy())
        else:
            mmses.append(row)

logging.info(f"Found {str(len(smses))} sms")
logging.info(f"Found {str(len(mmses))} mms")

time.sleep(3)

insert_sms_query = """insert into sms (thread_id, recipient_id, date_sent, date_received, read, type, body, receipt_timestamp)
        values (:thread_id, :recipient_id, :date_sent, :date_sent, :read, :type, :body, :date_sent)"""
insert_mms_query = """insert into mms (thread_id, date_sent, date_received, read, body, recipient_id, type, subscription_id, st)
            values (:thread_id, :date_sent, :date_sent, :read, :body, :recipient_id, :type, :subscription_id, :st) returning _id"""
insert_part_query = """insert into part (mid, seq, ct, pending_push, data_size, file_name, unique_id, caption, transform_properties)
            values (:mid, :seq, :ct, :pending_push, :data_size, :file_name, :unique_id, :caption, :transform_properties) returning _id"""

i = 0

if args.merge:
    logging.info("Deleting existing mms to replace")
    for r in mmses:
        if not len(r.get('add_list')):
            r['add_list'].append(r.get('recipient_id'))
        add_list = r.get('add_list')
        for add in add_list:
            logging.debug(f"Deleting existing mms to be replaced: {r['date_sent']} / {add}")
            cursor.execute(f"select _id as mms_id from mms where recipient_id = {add} and date_sent = {r['date_sent']}")
            result = cursor.fetchall()
            for row in result:
                mms_id = row[0]
                cursor.execute(f"select _id, unique_id from part where mid = '{mms_id}'")
                parts = cursor.fetchall()
                for part in parts:
                    part_id = part[0]
                    unique_id = part[1]
                    fname = os.path.join(output, f"Attachment_{part_id}_{unique_id}.bin")
                    fname2 = os.path.join(output, f"Attachment_{part_id}_{unique_id}.sbf")
                    try:
                        os.remove(fname)
                    except Exception:
                        pass
                    try:
                        os.remove(fname2)
                    except Exception:
                        pass
                cursor.execute(f"delete from part where mid = '{mms_id}'")
                cursor.execute(f"delete from mms where _id = '{mms_id}'")
        i += 1
        if i % 1000 == 0:
            conn.commit()
    try:
        conn.commit()
    except Exception:
        pass

logging.info("inserting mms")

for r in mmses:
    thread_id = get_or_make_thread(cursor, r)
    logging.debug(f"Writing MMS: {r}")
    r['thread_id'] = thread_id
    r['read'] = 1
    cursor.execute(insert_mms_query, r)
    (mms_id, ) = cursor.fetchone()
    seq = 0
    logging.debug(f"PARTS found {len(r['parts'])} parts")
    for part in r['parts']:
        # skip smil - not sure if Signal undestand SMIL formatting
        if int(part.attrib.get("seq", 0)) != -1 and part.attrib.get("data"):
            logging.debug(f"  Working on mms {mms_id} part number {seq}")
            data = base64.b64decode(part.attrib["data"])
            data_size = len(data)
            file_name = part.attrib.get("name", part.attrib.get("cl", ""))
            # something needs to be tweaked around here, file names are still coming across as null
            if file_name == "" or file_name == "null":
                file_name = part.attrib.get("cid", "")
            file_name = (
                file_name.replace("&lt;", "")
                .replace("&gt;", "")
                .replace("<", "")
                .replace(">", "")
            )
            unique_id = int(time.time() * 1000)
            logging.debug(f"    -> file: {file_name} is {data_size} bytes")
            caption = part.attrib.get("text", "")
            props = '{"skipTransform":true,"videoTrim":false,"videoTrimStartTimeUs":0,"videoTrimEndTimeUs":0,"sentMediaQuality":0,"videoEdited":false}'
            p = {
                "mid": mms_id,
                "seq": seq,
                "ct": part.attrib.get("ct"),
                "pending_push": 0,
                "data_size": data_size,
                "file_name": file_name,
                "unique_id": unique_id,
                "caption": None if caption in ['null', ''] else caption,
                "transform_properties": props,
            }
            cursor.execute(insert_part_query, p)
            (part_id, ) = cursor.fetchone()
            seq += 1
            # dump the attachments in the folder the way that signalbackup-tools likes to have them
            fname = f"{output}/Attachment_{part_id}_{unique_id}.bin"
            fname2 = f"{output}/Attachment_{part_id}_{unique_id}.sbf"
            with open(fname, "wb") as f:
                logging.debug(f"      * writing: {fname}")
                f.write(data)
            fdesc = f"ROWID:uint64:{part_id}\n\
ATTACHMENTID:uint64:{unique_id}\n\
LENGTH:uint32:{data_size}"
            with open(fname2, "w") as f:
                logging.debug(f"      * writing: {fname2}")
                f.write(fdesc)
    i += 1
    if i % 1000 == 0:
        conn.commit()
try:
    conn.commit()
except Exception:
    pass

logging.info("mms inserted")

if args.merge:
    logging.info("Deleting existing sms to be replaced")
    cursor.execute("create index if not exists sms_del on sms (recipient_id, date_sent);")
    for r in smses:
        logging.debug(f"Deleting existing sms to be replaced: {r['date_sent']} / {r['recipient_id']}")
        cursor.execute(f"delete from sms where recipient_id = '{r['recipient_id']}' and date_sent = '{r['date_sent']}';")
        i += 1
        if i % 1000 == 0:
            conn.commit()
    conn.commit()
    cursor.execute("drop index if exists sms_del;")

logging.info("inserting sms")

for r in smses:
    r['thread_id'] = get_or_make_thread(cursor=cursor, r=r)
    logging.debug(f"Writing SMS: {r}")
    cursor.execute(insert_sms_query, r)
    i += 1
    if i % 1000 == 0:
        conn.commit()
conn.commit()

logging.info("sms inserted")
conn.commit()
cursor.close()

logging.info("complete")
