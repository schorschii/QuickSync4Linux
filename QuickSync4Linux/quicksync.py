#!/usr/bin/env python3

from pathlib import Path
import configparser

import serial
import time
import struct
import argparse
import datetime
import re
import sys

from . import at
from . import obex
from .__init__ import __version__


def main():
    # read config
    config = {}
    configParser = configparser.ConfigParser()
    configParser.read(str(Path.home())+'/.config/quicksync4linux.ini')
    if(configParser.has_section('general')): config = dict(configParser.items('general'))

    # parse arguments
    parser = argparse.ArgumentParser(
        prog='QuickSync4Linux',
        description='Communicate with Gigaset devices',
        epilog=f'Version {__version__}, (c) Georg Sieber 2023-2024. If you like this program please consider making a donation using the sponsor button on GitHub (https://github.com/schorschii/QuickSync4Linux) to support the development. It depends on users like you if this software gets further updates.'
    )
    parser.add_argument('action', help='one of: info, obexinfo, dial, getcontacts, createcontact, editcontact, deletecontact, listfiles, upload, download, delete')
    parser.add_argument('options', nargs='?', help='e.g. a phone number for the "dial" action, a luid for contact operations or a file name on device for file actions')
    parser.add_argument('-d', '--device', default=config.get('device', '/dev/ttyACM0'), help='serial port device')
    parser.add_argument('-b', '--baud', default=config.get('baud', 9600))
    parser.add_argument('-f', '--file', default='-', help='file to read from or write into, stdout/stdin default')
    parser.add_argument('-v', '--verbose', action='count', default=0, help='print complete AT/Obex serial communication')
    args = parser.parse_args()

    # open serial port
    ser = serial.Serial(
        args.device,
        args.baud,
        write_timeout=at.Delay.TimeoutWrite
    )
    if(args.verbose): print('Connected to:', ser.name)

    def readVcfFile(path):
        vcf = open(path, 'rb').read()
        return vcf.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n') # ensure CRLF line breaks

    def sendAndReadResponse(data, wait=None, isObex=False):
        if(args.verbose):
            print()
            print('=== SEND ===')
            if(args.verbose >= 2): print(data.hex())
            print(data.decode('ascii', errors='backslashreplace'))
        ser.write(data)

        if(args.verbose):
            print()
            print('=== RECEIVE ===')
        results = []
        buf = b''
        while True:
            defaultDelay = at.Delay.AfterInvoke if(not isObex) else 0
            time.sleep(wait if(wait) else defaultDelay)

            if(ser.in_waiting == 0 and not wait): continue

            tmp = ser.read(ser.in_waiting)
            buf += tmp
            if(args.verbose):
                if(args.verbose >= 2): print(tmp.hex())
                print(tmp.decode('ascii', errors='backslashreplace'), end='')

            if(isObex): # obex command result handling
                try:
                    if(obex.evaluateResponse(buf, results, ser, isObex==obex.QuickSyncOperation.Upload)):
                        return b''.join(results)
                    else:
                        buf = b''
                except obex.InvalidObexLengthException:
                    # incomplete transmission, read more bytes from serial port
                    continue

            else: # AT command result handling
                try:
                    return at.evaluateResponse(buf, data)
                except at.IncompleteAtResponseException:
                    # incomplete transmission, read more bytes from serial port
                    continue


    if(args.action == 'info'):
        for title, command in {
            'Manufacturer': at.Command.GetManufacturer,
            'Type': at.Command.GetDeviceType,
            'Product': at.Command.GetProductName,
            'Serial': at.Command.GetSerialNumber,
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
                response = sendAndReadResponse(at.formatCommand(command)).decode('ascii')
            except Exception as e:
                response = '['+'ERROR: '+str(e)+']'
            print(title+':', response)


    elif(args.action == 'obexinfo'):
        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        print()
        print('===', obex.FilePath.InfoLog)
        print(sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Get+obex.Mask.Final,
                obex.compileNameHeader( obex.FilePath.InfoLog )
            ),
            isObex=True
        ).decode('utf8'))

        print()
        print('===', obex.FilePath.DevInfo)
        print(sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Get+obex.Mask.Final,
                obex.compileNameHeader( obex.FilePath.DevInfo )
            ),
            isObex=True
        ).decode('utf8'))

        print()
        print('===', obex.FilePath.LuidCC)
        print(sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Get+obex.Mask.Final,
                obex.compileNameHeader( obex.FilePath.LuidCC )
            ),
            isObex=True
        ).decode('utf8'))

        print()
        print('===', obex.FilePath.Luid0)
        print(sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Get+obex.Mask.Final,
                obex.compileNameHeader( obex.FilePath.Luid0 )
            ),
            isObex=True
        ).decode('utf8'))

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    elif(args.action == 'dial'):
        if(not args.options):
            raise Exception('Please tell me a number to call')

        sendAndReadResponse(at.formatCommand(at.Command.Dial, args.options), wait=0)


    elif(args.action == 'getcontacts'):
        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        vcf = sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Get+obex.Mask.Final,
                obex.compileNameHeader( obex.FilePath.PhoneBook )
            ),
            isObex=True
        ).decode('utf8')
        if(args.file == '-' or args.file == ''):
            print(vcf)
        else:
            with open(args.file, 'w') as f:
                f.write(vcf)

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    elif(args.action == 'createcontacts'):
        if(args.file == ''):
            raise Exception('Please give a .vcf file for import via --file parameter')
        elif(args.file == '-'):
            vcf = sys.stdin.read()
        else:
            vcf = readVcfFile(args.file).decode('utf8')

        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        counter = 1
        for vcard in re.findall("BEGIN\:VCARD[\S\s]*?END\:VCARD", vcf):
            if(not args.verbose): print('Creating contact #{0}'.format(counter))
            vcardBytes = vcard.encode('ascii')
            sendAndReadResponse(
                obex.compileMessage(
                    obex.OpCode.Put+obex.Mask.Final,
                    obex.compileNameHeader( obex.FilePath.NewVCardGQS )
                    + obex.compileLengthHeader( len(vcardBytes) )
                    + obex.compileMessage( obex.Header.EndOfBody, vcardBytes )
                ),
                isObex=True
            )
            counter += 1

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    elif(args.action == 'editcontact'):
        if(args.file == '-' or args.file == ''):
            raise Exception('Please give a .vcf file for import via --file parameter')
        if(not args.options):
            raise Exception('Please give the luid of the contact which should be edited')
        vcf = readVcfFile(args.file)

        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Put+obex.Mask.Final,
                obex.compileNameHeader( obex.FilePath.VCardLuid.format(args.options) )
                + obex.compileLengthHeader( len(vcf) )
                + obex.compileMessage( obex.Header.EndOfBody, vcf )
            ),
            isObex=True
        )

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    elif(args.action == 'deletecontact'):
        if(not args.options):
            raise Exception('Please give the luid of the contact which should be edited')

        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Put+obex.Mask.Final,
                obex.compileNameHeader( obex.FilePath.VCardLuid.format(args.options) )
            ),
            isObex=True
        )

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    elif(args.action == 'listfiles'):
        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        totalSpaceResponseBytes = sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Get+obex.Mask.Final,
                obex.compileMessage( obex.Header.AppParameters, obex.AppParametersCommand.MemoryStatusTotal )
            ),
            isObex=True
        )
        freeSpaceResponseBytes = sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Get+obex.Mask.Final,
                obex.compileMessage( obex.Header.AppParameters, obex.AppParametersCommand.MemoryStatusFree )
            ),
            isObex=True
        )
        print('Total Space:', obex.parseMemoryResponse(totalSpaceResponseBytes)/1024, 'KiB')
        print('Free Space:', obex.parseMemoryResponse(freeSpaceResponseBytes)/1024, 'KiB')

        for folder in [
            obex.FolderPath.ScreenSavers,
            obex.FolderPath.ClipPictures,
            obex.FolderPath.Ringtones,
        ]:
            print()
            print('===', folder)
            sendAndReadResponse(
                obex.compileMessage(
                    obex.OpCode.SetPath,
                    struct.pack('B', obex.SetPathFlags.DontCreate)
                    + struct.pack('B', obex.SetPathFlags.Constants)
                    + obex.compileNameHeader( folder )
                ),
                isObex=True
            )
            fileList = sendAndReadResponse(
                obex.compileMessage(
                    obex.OpCode.Get+obex.Mask.Final,
                    obex.compileMessage( obex.Header.Type, obex.ObjectMimeType.FolderListing )
                ),
                isObex=True
            ).decode('utf8')
            files, maxLenName = obex.parseFileListXml(''.join(fileList))
            for file in files:
                print(
                    (file['fileid']+':').ljust(4),
                    file['name'].ljust(maxLenName),
                    datetime.datetime.strptime(file['modified'], '%Y%m%dT%H%M%S').strftime('%Y-%m-%d %H:%M'),
                    file['user-perm'],
                    str(round(int(file['size'])/1024, 1)) + ' KiB'
                )

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    elif(args.action == 'download'):
        if(not args.options):
            raise Exception('Please give the file name of the file which should be downloaded')
        if(args.file == '-' or args.file == ''):
            raise Exception('Please specify the output file name via --file parameter')

        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        fileContent = sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Get+obex.Mask.Final,
                obex.compileNameHeader( args.options )
            ),
            isObex=True
        )
        with open(args.file, 'wb') as f:
            f.write(fileContent)

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    elif(args.action == 'upload'):
        if(not args.options):
            raise Exception('Please give the file name of the file which should be uploaded')
        if(args.file == '-' or args.file == ''):
            raise Exception('Please specify the input file via --file parameter')

        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        with open(args.file, 'rb') as f:
            data = f.read()
            chunkSize = 958
            counter = 0
            chunks = [data[i:i+chunkSize] for i in range(0, len(data), chunkSize)]
            for chunk in chunks:
                finalFlag = obex.Mask.Final if(counter == len(chunks)-1) else 0
                bodyHeader = obex.Header.EndOfBody if(counter == len(chunks)-1) else obex.Header.Body
                nameHeader = obex.compileNameHeader(args.options) if(counter == 0) else b''
                lengthHeader = obex.compileLengthHeader(len(data)) if(counter == 0) else b''
                sendAndReadResponse(
                    obex.compileMessage(
                        obex.OpCode.Put+finalFlag,
                        nameHeader
                        + lengthHeader
                        + obex.compileMessage( bodyHeader, chunk )
                    ),
                    isObex=obex.QuickSyncOperation.Upload
                )
                counter += 1

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    elif(args.action == 'delete'):
        if(not args.options):
            raise Exception('Please give the file name of the file which should be deleted')

        sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)
        sendAndReadResponse(
            obex.compileConnect(obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)),
            isObex=True
        )

        sendAndReadResponse(
            obex.compileMessage(
                obex.OpCode.Put+obex.Mask.Final,
                obex.compileNameHeader( args.options )
            ),
            isObex=True
        )

        time.sleep(at.Delay.ObexBoundary)
        sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)
        time.sleep(at.Delay.AfterExitObex)
        sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


    else:
        print('Unknown action: {0}'.format(args.action))
        exit(1)


if __name__ == "__main__":
    main()
