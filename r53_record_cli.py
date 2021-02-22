import boto3
import sys
import time
import os
import copy
from rich.columns import Columns
from rich import print as rprint
from rich.table import Table
from rich.console import Console, ScreenContext, RenderGroup
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.style import Style
from rich.color import Color
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.traceback import install
install()

HOSTED_ZONE_ID=<ADD HOSTED ZONE ID HERE>

# This returns fast enough (~900 records right now) that we can call it without issues
def get_all_records(client, next_item="."):
    record_list = []
    with console.status("Fetching Records from AWS", spinner="dots"):
        while True:
            resp = client.list_resource_record_sets(HostedZoneId=HOSTED_ZONE_ID, MaxItems="300", StartRecordName=next_item)
            record_list += resp['ResourceRecordSets']
            if resp.get('NextRecordName', None):
                next_item = resp['NextRecordName']
            else:
                break

    return record_list


# Filter record list to just latency records based on 'Region' field existence
def get_latency_records(record_list):
    with console.status("Searching for Latency Records", spinner="dots"):
        latency_record_list = []
        for record in record_list:
            if record.get('Region', None):
                latency_record_list.append(record)

    return latency_record_list


# Filter record list to just weighted records based on 'Weight' field existence
def get_weighted_records(record_list):
    with console.status("Searching for Weighted Records", spinner="dots"):
        weighted_record_list = []
        for record in record_list:
            if record.get('Weight', None):
                weighted_record_list.append(record)

    return weighted_record_list


# Create a table for displaying all records of all types
def create_all_record_table(record_list, title="Record List"):
    table = Table(title=title)
    table.add_column("Index")
    table.add_column("Record Name", justify="right")
    table.add_column("Value", justify="left")
    table.add_column("Type")
    table.add_column("Alias", justify="center")

    count = 0
    for record in record_list:
        if count % 2 == 0:
            color = Color.from_ansi(236)
        else:
            color = Color.from_ansi(232)

        try:
            table.add_row(str(count), record['Name'], ",".join([v['Value'] for v in record['ResourceRecords']]), record['Type'], None, style=Style(bgcolor=color))
        except KeyError:
            table.add_row(str(count), record['Name'], record['AliasTarget']['DNSName'], record['Type'], "Y", style=Style(bgcolor=color))
        count += 1


    return table


# Create a table for displaying letency records
def create_latency_record_table(record_list, title="Latency Record List", bgcolors=(236,232)):
    table = Table(title=title)
    table.add_column("Index")
    table.add_column("Record Name", justify="right")
    table.add_column("Value", justify="left")
    table.add_column("Region", justify="left")
    table.add_column("Alias", justify="center")

    count = 0
    for record in record_list:
        if count % 2 == 0:
            color = Color.from_ansi(bgcolors[0])
        else:
            color = Color.from_ansi(bgcolors[1])

        try:
            table.add_row(str(count), record['Name'], ",".join([v['Value'] for v in record['ResourceRecords']]), record['Region'], None, style=Style(bgcolor=color))
        except KeyError:
            table.add_row(str(count), record['Name'], record['AliasTarget']['DNSName'], record['Region'], "Y", style=Style(bgcolor=color))
        count += 1

    return table


# Create a table for displaying weighted records
def create_weighted_record_table(record_list, title="Weighted Record List", bgcolors=(236,232)):
    table = Table(title=title)
    table.add_column("Index")
    table.add_column("Record Name", justify="right")
    table.add_column("Value", justify="left")
    table.add_column("Weight", justify="left")

    count = 0
    for record in record_list:
        if count % 2 == 0:
            color = Color.from_ansi(bgcolors[0])
        else:
            color = Color.from_ansi(bgcolors[1])
        table.add_row(str(count), record['Name'], ",".join([v['Value'] for v in record['ResourceRecords']]), str(record['Weight']), style=Style(bgcolor=color))
        count += 1

    return table


# Display all records in paginated new screen
def display_all_records(record_list):
    table = create_all_record_table(record_list)
    with console.screen():
        with console.pager(styles=True):
            with console.status("Building Records Table", spinner="dots"):
                console.print(table)


# Display latency records in paginated new screen
def display_latency_records(record_list):
    latency_record_list = get_latency_records(record_list)
    table = create_latency_record_table(latency_record_list)
    with console.screen():
        with console.pager(styles=True):
            console.print(table)


# Display weighted records in paginated new screen
def display_weighted_records(record_list):
    weighted_record_list = get_weighted_records(record_list)
    table = create_weighted_record_table(weighted_record_list)
    with console.screen():
        with console.pager(styles=True):
            console.print(table)


# Filter a list of records by 'Name' field and filter string. Return new list of matches. ':<int>' returns record with that index
def filter_list(record_list, filter_string):
    filtered_list = []
    if filter_string.startswith(":"):
        index_filter = int(filter_string.split(":")[1]) # select the index based on the special ':<int>' format
        try:
            filtered_list.append(record_list[index_filter])
            return filtered_list
        except IndexError:
            return record_list

    for record in record_list:
        if filter_string in record['Name']:
            filtered_list.append(record)

    if not filtered_list: # If the resultant list is empty, dont return it. Instead return the last populated list
        return record_list
    return filtered_list


def edit_weight_records_by_filter(record_list, staged_changes):

    weighted_record_list = get_weighted_records(record_list) # get only weighted record sets
    filtered_list = weighted_record_list # weighted_list is our fallback. Set both lists to equal initially
    weighted_record_table = create_weighted_record_table(weighted_record_list)

    with ScreenContext(console, hide_cursor=True) as sc: # Use screen context to refresh renderables later
        sc.update(weighted_record_table) # display weighted records 

        while True: # keep allowing user to drill down into records based on search strings (each builds upon the last unless '..' is entered)  
            search_filter = Prompt.ask("Filter ('..' to reset filter, Enter to use current selection)")
            if search_filter == "..": # if user enters '..' return full list of weighted records again 
                filtered_list = weighted_record_list
                sc.update(weighted_record_table)
            elif search_filter == "": # The user has filtered choice to records shown on screen and presses enter with no other chars
                selection_table = create_weighted_record_table(filtered_list, title="Selected Records", bgcolors=(136, 132))
                sc.update(selection_table) # Display selected choices w new background colors

                valid_weight = False # wait for valid weight value for records to be entered 
                while not valid_weight:
                    weight_setting = IntPrompt.ask("Enter weight to set selected records to (0-255)")
                    if weight_setting in range(0,256):
                        valid_weight = True
                    else:
                        rprint("[yellow]Enter a valid record weight between 0-255")

                original_record_list = copy.deepcopy(filtered_list) # create a copy of the original filtered record selection  
                updated_record_list = update_selected_record_weights(filtered_list, weight_setting) # update in-memory records with the changes
                update_table = create_weighted_record_table(updated_record_list, title="Record Updates", bgcolors=(66, 62)) # Create update table with new weights shown

                render_group = RenderGroup(selection_table, update_table) # group original selection table and new updated table so they can be displayed at the same time on screen
                sc.update(render_group)

                # Confirm that changes should be staged
                if Confirm.ask("Stage changes"):
                    staged_changes.append((original_record_list, updated_record_list))
                    return staged_changes
                else:
                    rprint("Update Cancelled")
                    time.sleep(2)
                    return staged_changes

            else: 
                filtered_list = filter_list(filtered_list, search_filter) # Further filter the current filtered list
                filtered_table = create_weighted_record_table(filtered_list)
                sc.update(filtered_table) # Display new filtered table


# Update the 'Weight' field of a locally cached record
def update_selected_record_weights(record_list, weight):
    for record in record_list:
        record['Weight'] = weight

    return record_list


# Update AWS records to match locally cached record changes
def update_records(client, staged_changes):
    result = get_staged_changes_view(staged_changes)
    with console.screen():
        if type(result) ==  Layout:
            console.print(result)
            if Confirm.ask("[green]Are you sure you want to apply these changes?"):
                record_list = [record for changeset in staged_changes for record in changeset[1]]
                changes = []
                for record in record_list:
                    recordset = {"Action":"UPSERT", "ResourceRecordSet": record}
                    changes.append(recordset)

                try:
                    resp = client.change_resource_record_sets(HostedZoneId=HOSTED_ZONE_ID, ChangeBatch = {'Comment':'MTA Update','Changes': changes})
                    status = resp['ResponseMetadata']['HTTPStatusCode']
# TODO add logging here
                    if status == 200:
                        console.clear()
                        rprint(Panel.fit("[green] Records Updated!"))
                        refresh_record_cache()
                        time.sleep(2)
                        staged_changes = []
                        return staged_changes
                        
                    else:
                        console.clear()
                        rprint(Panel.fit("[red] Error updating records! \n {0}".format(resp)))
                        os.system('read -s -n 1 -p "Press any key to continue..."')
                        return staged_changes
                except Exception as e:
                    rprint("[red]Error updating records: \n {0}".format(e))
                    os.system('read -s -n 1 -p "Press any key to continue..."')
                    return staged_changes

            else:
                console.print("[yellow]Cancelling Apply")
                return staged_changes
        else:
            console.print(result)
            os.system('read -s -n 1 -p "Press any key to continue..."')
            return staged_changes


def edit_staged_changes(staged_changes):
    with ScreenContext(console, hide_cursor=True) as sc:
        while True:
            result = get_staged_changes_view(staged_changes)
            if type(result) == Layout:
                sc.update(result)
                delete_choice = Prompt.ask("Select an index to delete from staged changes. 'q' to exit")
                if delete_choice != "q":
                    try:
                        delete_choice = int(delete_choice)
                        original_records = [record for changeset in staged_changes for record in changeset[0]]
                        update_records = [record for changeset in staged_changes for record in changeset[1]]
                        if delete_choice not in range(0, len(update_records) + 1):
                            rprint("[yellow]Enter a valid index or 'q' to exit")
                        else:
                            original_records.remove(original_records[delete_choice])
                            update_records.remove(update_records[delete_choice])
                            if len(update_records) == 0:
                                staged_changes = []
                            else:
                                staged_changes = [(original_records, update_records)]
                    except ValueError:
                        rprint("[yellow]Enter a valid index or 'q' to exit")
                else:
                    break
            else:
                sc.update(result)
                os.system('read -s -n 1 -p "Press any key to continue..."')
                break

        return staged_changes


def get_staged_changes_view(staged_changes):
    if staged_changes:
        original_records = [record for changeset in staged_changes for record in changeset[0]]
        update_records = [record for changeset in staged_changes for record in changeset[1]]
        orig_table = create_weighted_record_table(original_records, title="Original Records", bgcolors=(160,160))
        update_table = create_weighted_record_table(update_records, title="Updated Records", bgcolors=(70,70))

        layout = Layout()
        layout.split(Layout(name="upper"), Layout(name="lower"))
        layout['upper'].size = 5
        layout['upper'].update("CHANGESETS")
        layout['lower'].split(
            Layout(orig_table,name="orig"),
            Layout(update_table, name="update"),
            direction="horizontal"
        )

        return layout

    else:
        return Panel("[yellow]No Changes Staged")


# Refresh local records cache from AWS
def refresh_record_cache():
    global RECORD_LIST
    RECORD_LIST = get_all_records(client)
    console.rule("[bold green]Record Cache Updated![/bold green]")
    time.sleep(2)


# Create main menu table
def get_menu_table():
    table = Table(title="Main Menu")
    table.add_column("Selection")
    table.add_column("Option Name")

    table.add_row("1",  "List All Records")
    table.add_row("2",  "List All Weighted Records")
    table.add_row("3",  "Update Weighted Records")
    table.add_row("4",  "List All Latency Records")
    table.add_row("7",  "View Staged Changes")
    table.add_row("8",  "Edit Staged Changes")
    table.add_row("9",  "Commit Changes")
    table.add_row("99", "Refresh Record Cache")
    table.add_row("0",  "Quit")

    return table


def main(staged_changes):
    quit = False
    while quit != True:
        main_menu = get_menu_table()
        console.clear()
        console.print(main_menu)
        menu_choice = IntPrompt.ask("Choose an option")
    
        if menu_choice == 0:
            if staged_changes:
                if Confirm.ask("[yellow]There are staged changes pending... Are you sure you want to quit before applying changes?"):
                    quit = True
                else:
                    quit = False
            else:
                quit = True
        else:
            if menu_choice == 1:
                display_all_records(RECORD_LIST)
    
            if menu_choice == 2:
                display_weighted_records(RECORD_LIST)

            if menu_choice == 3:
                staged_changes = edit_weight_records_by_filter(RECORD_LIST, staged_changes)
    
            if menu_choice == 4:
                display_latency_records(RECORD_LIST)
    
            if menu_choice == 7:
                result = get_staged_changes_view(staged_changes)
                with console.screen():
                    console.print(result)
                    os.system('read -s -n 1 -p "Press any key to continue..."')

            if menu_choice == 8:
                staged_changes = edit_staged_changes(staged_changes)

            if menu_choice == 9:
                staged_changes = update_records(client, staged_changes)

            if menu_choice == 99:
                refresh_record_cache()
    

if __name__=="__main__":
    console = Console()
    console.clear()
    client = boto3.client("route53")
    RECORD_LIST = get_all_records(client) # Fetch records only once on start and keep in memory for editing later
    staged_changes = []

    main(staged_changes)
