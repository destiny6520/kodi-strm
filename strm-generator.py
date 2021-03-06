# The main script responsible for generating .strm files as needed.

import sys
from os import getcwd, mkdir, system
from os.path import basename, dirname, exists, isdir, join
from pickle import dump as dump_pickle
from pickle import load as load_pickle
from re import match, sub
from shutil import rmtree
from time import sleep
from typing import Dict, List, Optional

from colorama import Fore, Style
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from reprint import output

files_scanned = 0
directories_scanned = 0
files_skipped = 0
bytes_scanned = 0


def authenticate() -> Resource:
    """
        A simple method to authenticate a user with Google Drive API. For the first
        run (or if script does not have the required permissions), this method will
        ask the user to login with a browser window, and then save a pickle file with
        the credentials obtained.

        For subsequent runs, this pickle file will be used directly, ensuring that the
        user does not have to login for every run of this script.

        Returns
        --------
        An instance of `Resource` that can be used to interact with the Google Drive API.
    """

    # Simple declartion, will be populated if credentials are present.
    creds: Optional[Credentials] = None

    # The scope that is to be requested.
    SCOPES = ['https://www.googleapis.com/auth/drive']

    if exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds: Credentials = load_pickle(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            dump_pickle(creds, token)

    service: Resource = build('drive', 'v3', credentials=creds)
    return service


def update(files: int, directories: int, skipped: int, size: int, out_stream):
    """
        Prints updates to the screen.

        Parameters
        -----------
        files: Integer containing the number of files that have been scanned. \n
        directories: Integer containing number of directories that have been scanned. \n
        skipped: Integer containing the number of files that have been skipped. \n
        size: Integer containing the raw size of files scanned (in bytes). \n
        out_stream: A dictionary object to which new lines are to be written as needed. \n
    """

    # The value of `size` will be an integer containing the raw size of the file(s) traversed
    # in bytes. Starting by converting this into a readable format.

    # An array size units. Will be used to convert raw size into a readble format.
    sizes = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB']

    counter = 0
    while size >= 1024:
        size /= 1024
        counter += 1

    # Forming a string containing readable size, this will be used to directly convert th e
    readable_size = '{:.3f} {}'.format(
        size,
        sizes[counter]
    )

    out_stream[2] = f'Directories Scanned: {directories}'
    out_stream[3] = f'Files Scanned: {files}'
    out_stream[4] = f'Bytes Scanned: {readable_size}'
    out_stream[5] = f'Files Skipped: {skipped}'


def select_teamdrive(service: Resource) -> str:
    """
        Allows the user to select the teamdrive for which strm files are to be generated.
        Will be used to let the user select a source incase a direct id is not supplied.

        Remarks
        --------
        Will internally handle any error/unexpected input. The only value returned by
        this method will be the id of the teamdrive that is to be used.

        Returns
        --------
        String containing the ID of the teamdrive selected by the user.
    """

    # Initializing as non-zero integer to ensure that the loop runs once.
    nextPageToken: str = None

    # Creating a list with the first element filled. Since the numbers being displayed
    # on the screen start from 1, filling the first element of the list with trash
    # ensures that the input from the user can used directly.
    teamdrives: List[str] = ['']

    counter: int = 1
    while True:
        result = service.drives().list(
            pageSize=100,
            pageToken=nextPageToken
        ).execute()

        for item in result['drives']:
            output = f'  {counter} ' + ('/' if counter % 2 else '\\')
            output += f'\t{item["name"]}{Style.RESET_ALL}'

            print(f'{Fore.GREEN if counter % 2 else Fore.CYAN}{output}')

            # Adding the id for this teamdrive to the list of teamdrives.
            teamdrives.append(item['id'])

            # Finally, incrementing the counter
            counter += 1

        try:
            nextPageToken = result['nextPageToken']
            if not nextPageToken:
                break
        except KeyError:
            # Key error will occur if there is no nextpage token -- breaking out of
            # the loop in such scenario.
            break

    # Adding an extra line.
    print()

    while True:
        print('Select a teamdrive from the list above.')
        try:
            id = input('teamdrive> ')

            if not match(r'^[0-9]+$', id):
                # Handling the scenario if the input is not an integer. Using regex
                # since direct type-cast to float will also accept floating-point,
                # which would be incorrect.
                raise ValueError

            id = int(id)
            if id <= 0 or id > len(teamdrives):
                # Handling the scenario if the input is not within the accepted range.
                # The zero-eth element of this list is garbage value, thus discarding
                # the input even at zero.
                raise ValueError

            # If the flow-of-control reaches here, the returning the id of the teamdrive
            # located at this position.
            return teamdrives[id]
        except ValueError:
            # Will reach here if the user enters a non-integer input
            print(f'\t{Fore.RED}Incorrect input detected. {Style.RESET_ALL}\n')


def shrink_path(full_path: str, max_len: int = 70) -> str:
    """
        Shrinks the path name to fit into a fixed number of characters.

        Parameters
        -----------
        full_path: String containing the full path that is to be printed. \n
        max_len: Integer containing the maximum length for the final path. Should be
        more than 10 characters. Default: 15 \n

        Returns
        --------
        String containing path after shrinking it. Will be atmost `max_len` characters
        in length.
    """

    if len(full_path) <= max_len:
        # Directly return the path if it fits within the maximum length allowed.
        return full_path

    allowed_len = max_len - 6
    return f'{full_path[:int(allowed_len / 2)]}......{full_path[-int(allowed_len / 2):]}'


def walk(origin_id: str, service: Resource, orig_path: str, item_details: Dict[str, str], out_stream,
         push_updates: bool, drive_path='~'):
    """
        Traverses directories in Google Drive and replicates the file/folder structure similar to
        Google Drive.

        This method will create an equvivalent `.strm` file for every video file found inside a
        particular directory. The end result will be the complete replication of the entire directory
        structure with just the video files with each file being an strm file pointing to the original
        file on the network.

        Parameters
        -----------
        origin_id: String containing the id of the original directory that is to be treated as the source.
        Every directory present inside this directory will be traversed, and a `.strm` file will be generated
        for every video file present inside this directory. \n
        service: Instance of `Resource` object used to interact with Google Drive API. \n
        orig_path: Path to the directory in which strm files are to be placed once generated. This directory
        will be made by THIS method internally. \n
        item_details: Dictionary containing details of the directory being scanned from Google drive. \n
        out_stream: Dictioanry object to which the output is to be written to once (if updates are needed). \n
        push_updates: Boolean indicating if updates are to be pushed to the screen or not. \n
    """

    global files_scanned, directories_scanned, bytes_scanned, files_skipped

    if not isinstance(origin_id, str) or not isinstance(service, Resource):
        raise TypeError('Unexpected argument type')

    # Updating the current path to be inside the path where this directory is to be created.
    cur_path = join(orig_path, item_details['name'])

    # Creating the root directory.
    mkdir(cur_path)

    page_token = None

    if push_updates:
        out_stream[0] = f'Scanning Directory: {shrink_path(drive_path)}/'
        out_stream[1] = '\n'  # Blank line

    while True:
        result = service.files().list(
            # Getting the maximum number of items avaiable in a single API call
            # to reduce the calls required.
            pageSize=1000,
            pageToken=page_token,

            # The fields that are to be included in the response.
            fields='files(name, id, mimeType, teamDriveId, size)',

            # Getting itesm from all drives, this allows scanning team-drives too.
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,

            # Skipping trashed files and directories
            q=f"'{origin_id}' in parents and trashed=false"
        ).execute()

        for item in result['files']:
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                # If the current object is a folder, incrementing the folder count and recursively
                # calling the same method over the new directory encountered.
                directories_scanned += 1

                walk(
                    origin_id=item['id'],
                    service=service,
                    orig_path=cur_path,
                    item_details=item,
                    out_stream=out_stream,
                    push_updates=push_updates,
                    drive_path=f'{join(drive_path, item["name"])}'
                )
            elif 'video' in item['mimeType'] or match(r'.*\.(mkv|mp4)$', item['name']):
                # Scanning the file, and creating an equivalent strm file if the file is a media file
                # Since the mime-type of files in drive can be modified externally, scanning the file
                # as a media file if the file has an extension of `.mp4` or `.mkv`.

                # Forming the string that is to be placed inside the strm file to ensure that the file
                # can be used by the drive add-on.
                file_content = f'plugin://plugin.googledrive/?action=play&item_id={item["id"]}'
                if 'teamDriveId' in item:
                    # Adding this part only for items present in a teamdrive.
                    file_content += f'&item_driveid={item["teamDriveId"]}&driveId={item["teamDriveId"]}'

                file_content += f'&content_type=video'
                with open(join(cur_path, item['name']+'.strm'), 'w+') as f:
                    f.write(file_content)

                # Updating the counter for files scanned as well as bytes scanned.
                files_scanned += 1
                bytes_scanned += int(item['size'])
            else:
                # Skipping the file if the file is not a video file (i.e. mime type does not match), and the
                # file does not have an extension of `.mp4` or `.mkv`. Updating the counter to increment the
                # number of files that have been skipped from the scan.
                files_skipped += 1

            if push_updates:
                # Updating counter on the screen if updates are to be pushed to the screen.
                update(
                    files=files_scanned,
                    directories=directories_scanned,
                    skipped=files_skipped,
                    size=bytes_scanned,
                    out_stream=out_stream
                )

        if 'nextPageToken' not in result:
            break


if __name__ == '__main__':
    # Starting by authenticating the connection.
    service = authenticate()

    destination = getcwd()
    source = None
    updates = True  # True by default.
    dir_name = None  # The name of the directory to store the strm files in.

    # Pattern(s) that are to be used to match against the source argument. The group is important since
    # this pattern is also being used to extract the value from argument.
    pattern_source = r'^--source=(.*)'
    pattern_output = r'^--updates="?(off|on)"?$'

    # TODO: Might want to differentiate between platforms here. Especially for custom directories.
    pattern_custom_dir = r'^--rootname="?(.*)"?$'
    pattern_dest = r'^--dest="?(.*)"?$'

    # Looping over all arguments that are passed to the script. The first (zeroe-th) value shall be the
    # name of the python script.
    for i in range(len(sys.argv)):
        if i == 0:
            # Skipping the first arguemnt, this would be the name of the python script file.
            continue

        if match(pattern_source, sys.argv[i]) and not source:
            # Pattern matching to select the source if the source is null. A better pattern match
            # can be obtained by ensuring that the only accepted value for the source is
            # alpha-numeric charater or hypen/underscores. Skipping this implementation for now as it
            # requires a testing.

            # Extracting id from the argument using substitution. Substituting everything from the
            # argument string except for the value :p
            source = match(pattern_source, sys.argv[i]).groups()[0]
        elif match(pattern_dest, sys.argv[i]):
            # Again, extracting the value using regex substitution.
            destination = match(pattern_dest, sys.argv[i]).groups()[0]

            if not isdir(destination):
                print(f'Error: `{sys.argv[i]}` is not a directory.\n')
                exit(10)  # Force quit.
        elif match(pattern_output, sys.argv[i]):
            # Switching the updates off if the argument has been passed.
            # Since the default value is to allow updates, no change is required incase
            # updates are explicitly being allowed.
            if match(pattern_output, sys.argv[i]).groups()[0] == 'off':
                updates = False
        elif match(pattern_custom_dir, sys.argv[i]):
            dir_name = match(pattern_custom_dir, sys.argv[i]).groups()[0]
        else:
            print(f'Unknown argument detected `{sys.argv[i]}`')
            exit(10)  # Non-zero exit code to indicate error.

    if not isinstance(source, str):
        # If a source has not been set, asking the user to select a teamdrive as root.
        source = select_teamdrive(service)

    # Attempting to get the details on the folder/teamdrive being used as the source.
    item_details = service.files().get(
        fileId=source,
        supportsAllDrives=True
    ).execute()

    if 'teamDriveId' in item_details and item_details['id'] == item_details['teamDriveId']:
        # If the source is a teamdrive, attempting to fetch details for the teamdrive instead.
        item_details = service.drives().get(
            driveId=item_details['teamDriveId']
        ).execute()

    if dir_name is not None:
        # If the name of the root directory is not set, using the name of the teamdrive/drive.
        item_details['name'] = dir_name

    # Clearing the destination directory (if it exists).
    final_path = join(destination, dir_name)
    if isdir(final_path):
        rmtree(final_path)

    print()  # Empty print.

    # Calling the method to walk through the drive directory.
    with output(output_type='list', initial_len=6, interval=0) as out_stream:
        # Creating the output object here to ensure that the same object is being used
        # for updates internally.

        walk(
            origin_id=source,
            service=service,
            orig_path=destination,
            item_details=item_details,
            out_stream=out_stream,
            push_updates=updates
        )

    print(f'\n\tTask completed. Saved the output at `{final_path}`')
