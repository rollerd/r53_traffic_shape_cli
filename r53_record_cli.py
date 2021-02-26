import boto3
from botocore.exceptions import ClientError
import json
import logging
import sys
import time
import os
import copy
from rich.columns import Columns
from rich import print as rprint
from rich.table import Table
from rich.console import Console, ScreenContext, RenderGroup
from rich.screen import Screen
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.style import Style
from rich.color import Color
from rich.layout import Layout
from rich.panel import Panel
from rich.traceback import install
install()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
fh = logging.FileHandler('logs/r53_updates.log')
fh.setLevel(logging.INFO)
fh.setFormatter(formatter)
logger.addHandler(fh)


HOSTED_ZONE_ID=os.getenv('AWS_HOSTED_ZONE_ID')
if not HOSTED_ZONE_ID:
    print("'AWS_HOSTED_ZONE_ID' env variable must be set")
    sys.exit(1)
os.environ['PAGER'] = 'less -r'

class Display(Console):
    def __init__(self):
        super().__init__()
        self.all_records_header = {'Index':'center', 'Name':'right', 'ResourceRecords':'left', 'AliasTarget':'left', 'Type':'center', 'TTL':'center', 'Weight':'center'}
        self.weighted_records_header = {'Index':'center', 'Name':'right', 'ResourceRecords':'left', 'Weight':'center'}
        self.latency_records_header = {'Index':'center', 'Name':'right', 'ResourceRecords':'left', 'AliasTarget':'left', 'Type':'center', 'TTL':'center', 'Weight':'center'}
        self.screen = ""

    def display_paginated(self, recordset, subtype=""):
        table = self.create_table(recordset.filtered_records, subtype)
        self.screen = ScreenContext(self, hide_cursor=True)
        with self.screen:
            with self.pager(styles=True):
                with self.status("Building Records Table", spinner="dots"):
                    self.print(table)
        self.screen = ""

    def split_display(self, left_display, right_display):
        layout = Layout()
        layout.split(Layout(name="upper"), Layout(name="lower"))
        layout['upper'].size = 5
        layout['upper'].update("\nCHANGESETS")
        layout['lower'].split(
            Layout(left_display,name="left"),
            Layout(right_display, name="right"),
            direction="horizontal"
        )

        self.update_screen(layout)

    def create_table(self, recordset, subtype, updated_records=False, bgcolors=[236,232]):
        title = "{0} Records List".format(subtype.title())
        table = Table(title=title)
        table_headers = getattr(self, subtype + "_records_header")

        for header, justify in table_headers.items():
            table.add_column(header, justify=justify)

        count = 0
        for record in recordset:
            if updated_records:
                record = record.get_updated_record()
            else:
                record = record.get_original_record()
            row_values = []
            for header in table_headers.keys():
                if header == 'Index':
                    row_values.append(str(count))
                elif record.get(header, None):
                    if header == 'ResourceRecords':
                        row_values.append(",".join([v['Value'] for v in record['ResourceRecords']]))
                    elif header == 'AliasTarget':
                        row_values.append(record['AliasTarget']['DNSName'])
                    else:
                        row_values.append(str(record[header]))
                else:
                    row_values.append("")
            if count % 2 == 0:
                color = Color.from_ansi(bgcolors[0])
            else:
                color = Color.from_ansi(bgcolors[1])
            table.add_row(*row_values, style=Style(bgcolor=color))
            
            count += 1
    
        return table

    def new_screen(self):
        self.set_alt_screen(enable=True)
        self.screen = Screen(style="")

    def update_screen(self, update):
        if not self.screen:
            self.new_screen()
        self.screen.renderable = update
        self.print(self.screen)

    def end_screen(self):
        self.set_alt_screen(enable=False)
        self.screen = ""


class Record:
    def __init__(self, data):
        self.updated_data = {} # add an updated_data field to house the updated fields and values
        self._parse_record(data)
    
    def _parse_record(self, data): # add each field of record as attribute of class object
        for k,v in data.items():
            self.__dict__.update({k:v})

    def reset(self):
        self.updated_data = {}

    def update(self, field, value): # add the updated field and value to the updated fields dict
        if field in self.__dict__:
            self.updated_data[field] = value

    def get_original_record(self): # return original data from self.__dict__ minus the updated_data dict
        original_data = copy.deepcopy(self.__dict__)
        try:
            original_data.pop("updated_data")
        except NameError:
            pass
        return original_data

    def get_updated_record(self): # return all fields with the updated values
        updated_record = copy.deepcopy(self.__dict__)
        for field in self.updated_data:
            updated_record[field] = self.updated_data[field]
        try:
            updated_record.pop("updated_data")
        except NameError:
            pass
        

        return updated_record


class RecordSet:
    def __init__(self):
        self.client = boto3.client('route53')
        self.all_records_list = []
        self.original_records = []
        self.filtered_records = []
        self.refresh_records()
        self.create_objects()

    def create_objects(self):
        for record in self.all_records_list:
            self.original_records.append(Record(record))

    # This returns fast enough (~900 records right now) that we can call it without issues
    def refresh_records(self, next_item=".", init=True):
        with display.status("Fetching Records from AWS", spinner="dots"):
            try:
                self.all_records_list = []
                self.original_records = []
                self.filtered_records = []
                resp = self.client.list_resource_record_sets(HostedZoneId=HOSTED_ZONE_ID, MaxItems="300", StartRecordName=next_item)
                self.all_records_list += resp['ResourceRecordSets']
                while resp.get('NextRecordName', None):
                    next_item = resp['NextRecordName']
                    resp = self.client.list_resource_record_sets(HostedZoneId=HOSTED_ZONE_ID, MaxItems="300", StartRecordName=next_item)
                    self.all_records_list += resp['ResourceRecordSets']
                if not init:
                    self.create_objects()
            except ClientError as e:
                if e.response['Error']['Code'] == "AccessDenied":
                    print('AccessDenied - check your AWS credentials')
                    sys.exit(1)
                else:
                    print(e)
                    sys.exit(1)

    def get_updated_records(self):
        updated_records = []
        for record in self.original_records:
            if record.updated_data:
                updated_records.append(record)

        return updated_records

    def dump_changeset(self, filename):
        updated_records = self.get_updated_records()
        updated_record_list = [x.get_updated_record() for x in updated_records]
        with open(f"changesets/{filename}.json", "w") as f:
            f.write(json.dumps(updated_record_list, indent=4))

        orig_record_list = [x.get_original_record() for x in updated_records]
        with open(f"changesets/{filename}_orig.json", "w") as f:
            f.write(json.dumps(orig_record_list, indent=4))


    def filter_records(self, field=None, filter_string=None):
        if field:
            self.filtered_records = []
            if field == 'All':
                self.filtered_records = self.original_records
            else:
                with display.status("Filtering {0} Records".format(field), spinner="dots"):
                    for record in self.original_records:
                        if record.__dict__.get(field, None):
                            self.filtered_records.append(record)
        if filter_string:
            if filter_string.startswith(":"):
                index_filter = int(filter_string.split(":")[1]) # select the index based on the special ':<int>' format
                try:
                    self.filtered_records = [self.filtered_records[index_filter]]
                except IndexError:
                    pass
            else:
                filtered_list = []
                for record in self.filtered_records:
                    if filter_string in record.Name:
                        filtered_list.append(record)

                if not filtered_list: # If the resultant list is empty, dont return it. Instead return the last populated list
                    pass
                else:
                    self.filtered_records = filtered_list

    def write_records(self):
            changes = []
            originals = []
            to_update = []
            updated_records = self.get_updated_records()
            for record in updated_records:
                change_record = {"Action":"UPSERT", "ResourceRecordSet": record.get_updated_record()}
                to_update.append(change_record)
                originals.append(record.get_original_record())
                changes.append(record.get_updated_record())
            try:
                resp = self.client.change_resource_record_sets(HostedZoneId=HOSTED_ZONE_ID, ChangeBatch = {'Comment':'MTA Update','Changes': to_update})
                status = resp['ResponseMetadata']['HTTPStatusCode']
                if status == 200:
                    logger.info("update: {0}\norig: {1}".format(changes, originals))
                    for record in updated_records:
                        record.reset()
                    return "\n[green] Records Updated!"
                    recordset.refresh_records()
                    os.system('read -s -n 1 -p "Press any key to continue..."')
                else:
                    return "\n[red] Error updating records! \n {0}".format(resp)
                    os.system('read -s -n 1 -p "Press any key to continue..."')
                    
            except Exception as e:
                return "\n[red]Error updating records: \n {0}".format(e)
                os.system('read -s -n 1 -p "Press any key to continue..."')


def load_changeset_from_file(recordset):
    changeset_file_list = os.listdir('changesets')

    table = Table(title="Changeset Files")
    table.add_column("Index")
    table.add_column("Filename")

    count = 0
    for filename in changeset_file_list:
        table.add_row(str(count), filename)
        count += 1

    display.update_screen(table) 
    file_choice = Prompt.ask("Choose a file. 'q' to quit to main menu")

    while True:
        if file_choice != 'q':
            try:
                if int(file_choice) not in range(0, len(changeset_file_list)):
                    display.end_screen()
                    display.update_screen(table)
                    rprint("[yellow]Choose a valid file index")
                    file_choice = Prompt.ask("Choose a file. 'q' to quit to main menu")
                else:
                    selected_file = changeset_file_list[int(file_choice)]
                    display.end_screen()
                    load_records(recordset, selected_file)
                    break
            except ValueError:
                    display.end_screen()
                    display.update_screen(table)
                    rprint("[yellow]Choose a valid file index")
                    file_choice = Prompt.ask("Choose a file. 'q' to quit to main menu")

        else:
            display.end_screen()
            break

    display.end_screen()


def match_original_record(recordset, change_record):
    for record in recordset.original_records:
        if change_record["Name"] == record.Name and change_record["ResourceRecords"] == record.ResourceRecords and change_record["Type"] == record.Type:
            record.updated_data = change_record


def load_records(recordset, filename):
    with open("changesets/{0}".format(filename), 'r') as f:
        data = json.loads(f.read())

    for change_record in data:
        match_original_record(recordset, change_record)


def edit_weight_records_by_filter(recordset):

    original_filtered_set = recordset.filtered_records # this is our fallback
    weighted_record_table = display.create_table(recordset.filtered_records, "weighted")

    display.update_screen(weighted_record_table)
    while True:
        search_filter = Prompt.ask("Filter ('..' to reset filter, 'Enter' to use current selection, ':<index>' for record #, ':q' to exit)")
        if search_filter == "..": # if user enters '..' return full list of weighted records again 
            recordset.filtered_records = original_filtered_set
            weighted_record_table = display.create_table(recordset.filtered_records, "weighted")
            display.update_screen(weighted_record_table)
        elif search_filter == ":q":
            display.end_screen()
            break
        elif search_filter == "": # The user has filtered choice to records shown on screen and presses enter with no other chars
            selection_table = display.create_table(recordset.filtered_records, "weighted", bgcolors=[136, 132])

            valid_weight = False # wait for valid weight value for records to be entered 
            error = 0
            while not valid_weight:
                display.update_screen(selection_table)
                if error:
                    rprint("[yellow]Enter a valid record weight between 0-255")
                weight_setting = IntPrompt.ask("Enter weight to set selected records to (0-255)")
                if weight_setting in range(0,256):
                    valid_weight = True
                else:
                    error = 1

            for record in recordset.filtered_records:
                record.update("Weight", weight_setting)

            update_table = display.create_table(recordset.filtered_records, 'weighted', updated_records=True, bgcolors=[66, 62]) # Create update table with new weights shown

            render_group = RenderGroup(selection_table, update_table) # group original selection table and new updated table so they can be displayed at the same time on screen
            display.update_screen(render_group)

            # Confirm that changes should be staged
            if Confirm.ask("Stage changes"):
                display.end_screen()
                break
            else:
                for record in recordset.filtered_records:
                    record.reset()
                rprint("Update Cancelled")
                time.sleep(2)
                display.end_screen()
                break
        else:
            recordset.filter_records(filter_string=search_filter)
            filtered_table = display.create_table(recordset.filtered_records, "weighted")
            display.update_screen(filtered_table)

    display.end_screen()


def get_staged_changes_view(recordset):
    updated_records = recordset.get_updated_records()
    if updated_records:
        orig_table = display.create_table(updated_records, "weighted", bgcolors=[160,160])
        update_table = display.create_table(updated_records, "weighted", updated_records=True, bgcolors=[70,70])

        display.split_display(orig_table, update_table)
        os.system('read -s -n 1 -p "Press any key to continue..."')
        display.end_screen()

    else:
        display.update_screen("\n[yellow]No Changes Staged")
        os.system('read -s -n 1 -p "Press any key to continue..."')
        display.end_screen()


def edit_staged_changes(recordset):
    updated_records = recordset.get_updated_records()
    if updated_records:
        orig_table = display.create_table(updated_records, "weighted", bgcolors=[160,160])
        update_table = display.create_table(updated_records, "weighted", updated_records=True, bgcolors=[70,70])

        display.split_display(orig_table, update_table)
        while True:
            if len(updated_records) == 0:
                os.system('read -s -n 1 -p "No more records staged. Press any key to continue..."')
                break
            else:
                delete_choice = Prompt.ask("Select an index to delete from staged changes. 'q' to exit")
            if delete_choice != "q":
                try:
                    delete_choice = int(delete_choice)
                    if delete_choice not in range(0, len(updated_records)):
                        display.end_screen()
                        display.split_display(orig_table, update_table)
                        rprint("[yellow]Enter a valid index or 'q' to exit")
                    else:
                        updated_records[delete_choice].reset()
                        updated_records = recordset.get_updated_records()
                        orig_table = display.create_table(updated_records, "weighted", bgcolors=[160,160])
                        update_table = display.create_table(updated_records, "weighted", updated_records=True, bgcolors=[70,70])
                        display.end_screen()
                        display.split_display(orig_table, update_table)
                except ValueError:
                    display.end_screen()
                    display.split_display(orig_table, update_table)
                    rprint("[yellow]Enter a valid index or 'q' to exit")
            else:
                display.end_screen()
                break
        display.end_screen()

    else:
        display.update_screen("\n[yellow]No Changes Staged")
        os.system('read -s -n 1 -p "Press any key to continue..."')
        display.end_screen()


# Update AWS records to match locally cached record changes
def update_records(recordset):
    updated_records = recordset.get_updated_records()
    if updated_records:
        orig_table = display.create_table(updated_records, "weighted", bgcolors=[160,160])
        update_table = display.create_table(updated_records, "weighted", updated_records=True, bgcolors=[70,70])
    
        display.split_display(orig_table, update_table)
        if Confirm.ask("[green]Are you sure you want to apply these changes?"):
            resp = recordset.write_records()
            display.update_screen(resp)
            os.system('read -s -n 1 -p "Press any key to continue..."')
            display.end_screen()
        else:
            display.update_screen("\n[yellow]Cancelling Apply")
            time.sleep(2)
            display.end_screen()
    else:
        display.update_screen("\n[yellow]No records staged for updating")
        os.system('read -s -n 1 -p "Press any key to continue..."')
        display.end_screen()


def dump_changesets(recordset):
    updated_records = recordset.get_updated_records()
    if updated_records:
        changeset_filename = Prompt.ask("\n[blue]Enter a filename to write changesets to (will create new and original records in two files)")
        recordset.dump_changeset(changeset_filename)
    else:
        display.update_screen("\n[yellow]No records staged for updating")
        os.system('read -s -n 1 -p "Press any key to continue..."')
        display.end_screen()



# Create main menu table
def get_menu_table():
    table = Table(title="Main Menu")
    table.add_column("Selection")
    table.add_column("Option Name")

    table.add_row("1",  "List All Records")
    table.add_row("2",  "List All Weighted Records")
    table.add_row("3",  "List All Latency Records")
    table.add_row("4",  "Update Weighted Records")
    table.add_row("5",  "Load Changeset From File")
    table.add_row("7",  "View Staged Changes")
    table.add_row("8",  "Edit Staged Changes")
    table.add_row("9",  "Commit Changes")
    table.add_row("10",  "Dump Changesets")
    table.add_row("99", "Refresh Record Cache")
    table.add_row("0",  "Quit")

    return table


def refresh_record_cache(recordset):
    recordset.refresh_records(init=False)
    display.clear()
    display.update_screen("[green]Records refreshed from AWS")
    time.sleep(2)
    display.end_screen()


def confirm_quit(recordset):
    updated_records = recordset.get_updated_records()
    if len(updated_records) > 0:
        if Confirm.ask("[yellow]There are staged changes pending... Are you sure you want to quit before applying changes?"):
            sys.exit(0)
    else:
        sys.exit(0)


def main():
    recordset = RecordSet()
    quit = False
    while quit != True:
        main_menu = get_menu_table()
        display.clear()
        display.update_screen(main_menu)
        menu_choice = IntPrompt.ask("Choose an option")
    
        if menu_choice == 0:
            display.clear()
            confirm_quit(recordset)

        if menu_choice == 1:
            display.clear()
            recordset.filter_records('All')
            display.display_paginated(recordset, 'all')

        if menu_choice == 2:
            display.clear()
            recordset.filter_records('Weight')
            display.display_paginated(recordset, 'weighted')

        if menu_choice == 3:
            display.clear()
            recordset.filter_records('Region')
            display.display_paginated(recordset, 'latency')

        if menu_choice == 4:
            display.clear()
            recordset.filter_records('Weight')
            edit_weight_records_by_filter(recordset)

        if menu_choice == 5:
            display.clear()
            load_changeset_from_file(recordset)

        if menu_choice == 7:
            display.clear()
            get_staged_changes_view(recordset)

        if menu_choice == 8:
            display.clear()
            edit_staged_changes(recordset)

        if menu_choice == 9:
            display.clear()
            update_records(recordset)

        if menu_choice == 10:
            display.clear()
            dump_changesets(recordset)

        if menu_choice == 99:
            refresh_record_cache(recordset)
    
    display.clear()
    display.end_screen()

if __name__=="__main__":
    display = Display()
    main()
