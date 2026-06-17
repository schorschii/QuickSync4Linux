#!/usr/bin/env python3

from pathlib import Path
import configparser
import datetime
import struct
import serial
import time
import argparse
import re
import sys

from . import at
from . import obex
from . import btserial
from .__init__ import __version__


# ─── Connection ───────────────────────────────────────────────────────────────

def open_connection(device: str, baud: int = 9600):
    """Open a serial or Bluetooth connection to the device.
    Returns a serial/BluetoothSerial object."""
    if btserial.isBluetoothAddress(device):
        mac, channel = btserial.parseBluetoothAddress(device)
        return btserial.BluetoothSerial(mac, channel, write_timeout=at.Delay.TimeoutWrite)
    else:
        return serial.Serial(device, baud, write_timeout=at.Delay.TimeoutWrite)


def close_connection(ser):
    """Close the serial/Bluetooth connection."""
    try:
        ser.close()
    except Exception:
        pass


# ─── Low-level communication ──────────────────────────────────────────────────

def send_and_read(ser, data, wait=None, is_obex=False, verbose=0):
    """Send data and read response from device."""
    if verbose:
        print()
        print('=== SEND ===')
        if verbose >= 2: print(data.hex())
        print(data.decode('ascii', errors='backslashreplace'))

    ser.write(data)

    if verbose:
        print()
        print('=== RECEIVE ===')

    results = []
    buf = b''
    while True:
        default_delay = at.Delay.AfterInvoke if not is_obex else 0
        time.sleep(wait if wait else default_delay)

        if ser.in_waiting == 0 and not wait:
            continue

        tmp = ser.read(ser.in_waiting)
        buf += tmp
        if verbose:
            if verbose >= 2: print(tmp.hex())
            print(tmp.decode('ascii', errors='backslashreplace'), end='')

        if is_obex:
            try:
                if obex.evaluateResponse(buf, results, ser, is_obex == obex.QuickSyncOperation.Upload):
                    return b''.join(results)
                else:
                    buf = b''
            except obex.InvalidObexLengthException:
                continue
        else:
            try:
                return at.evaluateResponse(buf, data)
            except at.IncompleteAtResponseException:
                continue


def _exit_obex(ser):
    """Exit OBEX mode and reset device."""
    time.sleep(at.Delay.ObexBoundary)
    send_and_read(ser, at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
    time.sleep(at.Delay.AfterExitObex)
    send_and_read(ser, at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


def _enter_obex_dessync(ser):
    """Enter OBEX mode and connect to DesSync service."""
    send_and_read(ser, at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
    send_and_read(ser,
        obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
        is_obex=True
    )


# ─── Device info ──────────────────────────────────────────────────────────────

def get_info(ser, verbose=0) -> str:
    """Query device info and return as formatted string."""
    lines = []
    for title, command in {
        'Manufacturer': at.Command.GetManufacturer,
        'Type': at.Command.GetDeviceType,
        'Product': at.Command.GetProductName,
        'Serial (IPUI)': at.Command.GetSerialNumber,
        'Internal Name': at.Command.GetInternalName,
        'Battery State': at.Command.GetBatteryState,
        'Signal State': at.Command.GetSignalState,
        'Firmware': at.Command.GetFirmwareVersion,
        'Firmware URL': at.Command.GetFirmwareUrl,
        'Melodies': at.Command.ListMelodies,
        'Area Codes': at.Command.GetAreaCodes,
        'Hardware Connection State': at.Command.GetHardwareConnectionState,
        'Supported Features': at.Command.GetSupportedFeatures,
        'Supported Multimedia': at.Command.GetSupportedMultimedia,
        'Screen Size Clip': at.Command.GetScreenSizeClip,
        'Screen Size Full': at.Command.GetScreenSizeFull,
        'Extended Modes List': at.Command.GetExtendedModesList,
        'Current Extended Mode': at.Command.GetCurrentExtendedMode,
    }.items():
        try:
            response = send_and_read(ser, at.formatCommand(command), verbose=verbose).decode('ascii')
        except Exception as e:
            response = '[ERROR: ' + str(e) + ']'
        lines.append(title + ': ' + response)
    return '\n'.join(lines)


def get_obex_info(ser, verbose=0) -> str:
    """Query OBEX device info and return as formatted string."""
    lines = []
    _enter_obex_dessync(ser)

    for path in [obex.FilePath.InfoLog, obex.FilePath.DevInfo,
                 obex.FilePath.LuidCC, obex.FilePath.Luid0]:
        lines.append('')
        lines.append('=== ' + path)
        lines.append(send_and_read(ser,
            obex.compileMessage(
                obex.OpCode.Get + obex.Mask.Final,
                obex.compileNameHeader(path)
            ),
            is_obex=True,
            verbose=verbose
        ).decode('utf8'))

    _exit_obex(ser)
    return '\n'.join(lines)


# ─── Contacts ─────────────────────────────────────────────────────────────────

def get_contacts(ser, verbose=0) -> bytes:
    """Download all contacts from device, return raw VCF bytes."""
    _enter_obex_dessync(ser)

    vcf = send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Get + obex.Mask.Final,
            obex.compileNameHeader(obex.FilePath.PhoneBook)
        ),
        is_obex=True,
        verbose=verbose
    )

    _exit_obex(ser)
    return vcf


def set_contacts(ser, vcf_data: bytes, verbose=0):
    """Upload VCF data (bytes) to device, replacing all contacts."""
    if isinstance(vcf_data, str):
        vcf_data = vcf_data.encode('utf-8')
    # Ensure CRLF line endings
    vcf_data = vcf_data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')

    _enter_obex_dessync(ser)

    send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Put + obex.Mask.Final,
            obex.compileNameHeader(obex.FilePath.PhoneBook)
            + obex.compileLengthHeader(len(vcf_data))
            + obex.compileMessage(obex.Header.EndOfBody, vcf_data)
        ),
        is_obex=True,
        verbose=verbose
    )

    _exit_obex(ser)


def create_contact(ser, vcf_data: bytes, verbose=0):
    """Create a new contact on device."""
    if isinstance(vcf_data, str):
        vcf_data = vcf_data.encode('utf-8')
    vcf_data = vcf_data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')

    _enter_obex_dessync(ser)

    send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Put + obex.Mask.Final,
            obex.compileNameHeader(obex.FilePath.NewVCardGQS)
            + obex.compileLengthHeader(len(vcf_data))
            + obex.compileMessage(obex.Header.EndOfBody, vcf_data)
        ),
        is_obex=True,
        verbose=verbose
    )

    _exit_obex(ser)


def edit_contact(ser, luid: str, vcf_data: bytes, verbose=0):
    """Edit an existing contact on device by luid."""
    if isinstance(vcf_data, str):
        vcf_data = vcf_data.encode('utf-8')
    vcf_data = vcf_data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')

    _enter_obex_dessync(ser)

    send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Put + obex.Mask.Final,
            obex.compileNameHeader(obex.FilePath.VCardLuid.format(luid))
            + obex.compileLengthHeader(len(vcf_data))
            + obex.compileMessage(obex.Header.EndOfBody, vcf_data)
        ),
        is_obex=True,
        verbose=verbose
    )

    _exit_obex(ser)


def delete_contact(ser, luid: str, verbose=0):
    """Delete a contact from device by luid."""
    _enter_obex_dessync(ser)

    send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Put + obex.Mask.Final,
            obex.compileNameHeader(obex.FilePath.VCardLuid.format(luid))
        ),
        is_obex=True,
        verbose=verbose
    )

    _exit_obex(ser)


# ─── Files ────────────────────────────────────────────────────────────────────

def list_files(ser, verbose=0) -> str:
    """List all files on device, return formatted string."""
    lines = []
    _enter_obex_dessync(ser)

    total_bytes = send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Get + obex.Mask.Final,
            obex.compileMessage(obex.Header.AppParameters, obex.AppParametersCommand.MemoryStatusTotal)
        ),
        is_obex=True,
        verbose=verbose
    )
    free_bytes = send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Get + obex.Mask.Final,
            obex.compileMessage(obex.Header.AppParameters, obex.AppParametersCommand.MemoryStatusFree)
        ),
        is_obex=True,
        verbose=verbose
    )
    lines.append('Total Space: ' + str(obex.parseMemoryResponse(total_bytes) / 1024) + ' KiB')
    lines.append('Free Space: ' + str(obex.parseMemoryResponse(free_bytes) / 1024) + ' KiB')

    for folder in [obex.FolderPath.ScreenSavers, obex.FolderPath.ClipPictures, obex.FolderPath.Ringtones]:
        lines.append('')
        lines.append('=== ' + folder)
        send_and_read(ser,
            obex.compileMessage(
                obex.OpCode.SetPath,
                struct.pack('B', obex.SetPathFlags.DontCreate)
                + struct.pack('B', obex.SetPathFlags.Constants)
                + obex.compileNameHeader(folder)
            ),
            is_obex=True,
            verbose=verbose
        )
        file_list_xml = send_and_read(ser,
            obex.compileMessage(
                obex.OpCode.Get + obex.Mask.Final,
                obex.compileMessage(obex.Header.Type, obex.ObjectMimeType.FolderListing)
            ),
            is_obex=True,
            verbose=verbose
        ).decode('utf8')
        files, max_len = obex.parseFileListXml(''.join(file_list_xml))
        for f in files:
            lines.append(
                (f['fileid'] + ':').ljust(4) + ' '
                + f['name'].ljust(max_len) + '  '
                + datetime.datetime.strptime(f['modified'], '%Y%m%dT%H%M%S').strftime('%Y-%m-%d %H:%M') + ' '
                + f['user-perm'] + ' '
                + str(round(int(f['size']) / 1024, 1)) + ' KiB'
            )

    _exit_obex(ser)
    return '\n'.join(lines)


def download_file(ser, remote_path: str, local_path: str, verbose=0):
    """Download a file from device to local path."""
    _enter_obex_dessync(ser)

    content = send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Get + obex.Mask.Final,
            obex.compileNameHeader(remote_path)
        ),
        is_obex=True,
        verbose=verbose
    )
    with open(local_path, 'wb') as f:
        f.write(content)

    _exit_obex(ser)


def upload_file(ser, remote_name: str, local_path: str, verbose=0):
    """Upload a local file to device."""
    with open(local_path, 'rb') as f:
        data = f.read()

    _enter_obex_dessync(ser)

    chunk_size = 958
    counter = 0
    chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]
    for chunk in chunks:
        final_flag = obex.Mask.Final if counter == len(chunks) - 1 else 0
        body_header = obex.Header.EndOfBody if counter == len(chunks) - 1 else obex.Header.Body
        name_header = obex.compileNameHeader(remote_name) if counter == 0 else b''
        length_header = obex.compileLengthHeader(len(data)) if counter == 0 else b''
        send_and_read(ser,
            obex.compileMessage(
                obex.OpCode.Put + final_flag,
                name_header + length_header + obex.compileMessage(body_header, chunk)
            ),
            is_obex=obex.QuickSyncOperation.Upload,
            verbose=verbose
        )
        counter += 1

    _exit_obex(ser)


def delete_file(ser, remote_path: str, verbose=0):
    """Delete a file from device."""
    _enter_obex_dessync(ser)

    send_and_read(ser,
        obex.compileMessage(
            obex.OpCode.Put + obex.Mask.Final,
            obex.compileNameHeader(remote_path)
        ),
        is_obex=True,
        verbose=verbose
    )

    _exit_obex(ser)


def dial(ser, number: str, verbose=0):
    """Dial a phone number."""
    send_and_read(ser, at.formatCommand(at.Command.Dial, number), wait=0, verbose=verbose)


# ─── CLI entry point ──────────────────────────────────────────────────────────

def main():
    # read config
    config = {}
    config_parser = configparser.ConfigParser()
    config_parser.read(str(Path.home()) + '/.config/quicksync4linuxgui.ini')
    if config_parser.has_section('general'):
        config = dict(config_parser.items('general'))

    # parse arguments
    parser = argparse.ArgumentParser(
        prog='QuickSync4Linux',
        description='Communicate with Gigaset devices',
        epilog=f'Version {__version__}, (c) Georg Sieber 2023-2024. '
               f'If you like this program please consider making a donation using the sponsor button on GitHub '
               f'(https://github.com/schorschii/QuickSync4Linux) to support the development. '
               f'It depends on users like you if this software gets further updates.'
    )
    parser.add_argument('action', help='one of: info, obexinfo, dial, getcontacts, setcontacts, '
                                       'createcontact, editcontact, deletecontact, listfiles, upload, download, delete')
    parser.add_argument('options', nargs='?', help='e.g. a phone number for "dial", a luid for contact '
                                                   'operations or a file name for file actions')
    parser.add_argument('-d', '--device', default=config.get('device', '/dev/ttyACM0'),
                        help='serial port device or Bluetooth MAC (optionally with @channel, default channel 1)')
    parser.add_argument('-b', '--baud', default=config.get('baud', 9600))
    parser.add_argument('-f', '--file', default='-', help='file to read from or write into, stdout/stdin default')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='print complete AT/Obex serial communication')
    args = parser.parse_args()

    # open connection
    try:
        ser = open_connection(args.device, args.baud)
    except (OSError, serial.SerialException) as e:
        print(f'✗ Connection to {args.device} failed: {e}', file=sys.stderr)
        sys.exit(1)
    if args.verbose:
        print('Connected to:', ser.name)

    try:
        if args.action == 'info':
            print(get_info(ser, verbose=args.verbose))

        elif args.action == 'obexinfo':
            print(get_obex_info(ser, verbose=args.verbose))

        elif args.action == 'dial':
            if not args.options:
                raise Exception('Please provide a number to call')
            dial(ser, args.options, verbose=args.verbose)

        elif args.action == 'getcontacts':
            if args.file == '-' or args.file == '':
                sys.stdout.buffer.write(get_contacts(ser, verbose=args.verbose))
            else:
                with open(args.file, 'wb') as f:
                    f.write(get_contacts(ser, verbose=args.verbose))

        elif args.action in ('setcontacts', 'createcontacts'):
            if args.file == '-' or args.file == '':
                vcf = sys.stdin.buffer.read()
            else:
                with open(args.file, 'rb') as f:
                    vcf = f.read()
            set_contacts(ser, vcf, verbose=args.verbose)

        elif args.action == 'createcontact':
            if args.file == '-' or args.file == '':
                vcf = sys.stdin.buffer.read()
            else:
                with open(args.file, 'rb') as f:
                    vcf = f.read()
            create_contact(ser, vcf, verbose=args.verbose)

        elif args.action == 'editcontact':
            if not args.options:
                raise Exception('Please provide the luid of the contact to edit')
            if args.file == '-' or args.file == '':
                vcf = sys.stdin.buffer.read()
            else:
                with open(args.file, 'rb') as f:
                    vcf = f.read()
            edit_contact(ser, args.options, vcf, verbose=args.verbose)

        elif args.action == 'deletecontact':
            if not args.options:
                raise Exception('Please provide the luid of the contact to delete')
            delete_contact(ser, args.options, verbose=args.verbose)

        elif args.action == 'listfiles':
            print(list_files(ser, verbose=args.verbose))

        elif args.action == 'download':
            if not args.options:
                raise Exception('Please provide the remote file name')
            if args.file == '-' or args.file == '':
                raise Exception('Please specify the output file via --file parameter')
            download_file(ser, args.options, args.file, verbose=args.verbose)

        elif args.action == 'upload':
            if not args.options:
                raise Exception('Please provide the remote file name')
            if args.file == '-' or args.file == '':
                raise Exception('Please specify the input file via --file parameter')
            upload_file(ser, args.options, args.file, verbose=args.verbose)

        elif args.action == 'delete':
            if not args.options:
                raise Exception('Please provide the remote file name to delete')
            delete_file(ser, args.options, verbose=args.verbose)

        else:
            print('Unknown action:', args.action)
            sys.exit(1)

    finally:
        close_connection(ser)


if __name__ == '__main__':
    main()