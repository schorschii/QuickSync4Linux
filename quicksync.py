#!/usr/bin/env python3

import serial
import time
import struct
import argparse

import at
import obex


# parse arguments
parser = argparse.ArgumentParser(
    prog='QuickSync4Linux',
    description='Communicate with Gigaset devices',
    epilog='(c) Georg Sieber 2023. If you like this program please consider making a donation using the sponsor button on GitHub (https://github.com/schorschii/QuickSync4Linux) to support the development. It depends on users like you if this software gets further updates.'
)
parser.add_argument('action', help='one of: info, dial, getcontacts, putcontact')
parser.add_argument('options', nargs='?', help='e.g. a phone number for the "dial" action')
parser.add_argument('-d', '--device', default='/dev/ttyACM0', help='serial port device')
parser.add_argument('-b', '--baud', default=9600)
parser.add_argument('-f', '--file', default='-', help='file to read from or write into, stdout/stdin default')
parser.add_argument('-v', '--verbose', action='count', default=0, help='print complete AT/Obex serial communication')
args = parser.parse_args()

# open serial port
ser = serial.Serial(args.device, args.baud)
if(args.verbose): print('Connected to:', ser.name)

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
        time.sleep(wait if wait else at.Delay.AfterInvoke)

        if(ser.in_waiting == 0 and not wait): continue

        tmp = ser.read(ser.in_waiting)
        buf += tmp
        if(args.verbose):
            if(args.verbose >= 2): print(tmp.hex())
            print(tmp.decode('ascii', errors='backslashreplace'), end='')

        if(isObex): # obex command result handling
            try:
                if(obex.evaluateResponse(buf, results, ser)):
                    return results
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


elif(args.action == 'dial'):
    if(not args.options):
        raise Exception('Please tell me a number to call')

    response = sendAndReadResponse(at.formatCommand(at.Command.Dial, args.options)).decode('ascii')


elif(args.action == 'getcontacts'):
    sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)

    sendAndReadResponse(
        obex.compileConnect(
            obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)
        ),
        isObex=True
    )

    #print(''.join(sendAndReadResponse(
    #    obex.compileMessage(
    #        obex.OpCode.Get+obex.Mask.Final,
    #        obex.compileNameHeader( obex.FilePath.InfoLog )
    #    ),
    #    isObex=True
    #)))

    vcf = ''.join(sendAndReadResponse(
        obex.compileMessage(
            obex.OpCode.Get+obex.Mask.Final,
            obex.compileNameHeader( obex.FilePath.PhoneBook )
        ),
        isObex=True
    ))
    if(args.file == '-' or args.file == ''):
        print(vcf)
    else:
        with open(args.file, 'w') as f:
            f.write(vcf)

    time.sleep(at.Delay.ObexBoundary)
    sendAndReadResponse(at.formatCommand(at.Command.ExitObex), wait=at.Delay.ObexBoundary)

    time.sleep(at.Delay.AfterExitObex)
    sendAndReadResponse(at.formatCommand(at.Command.Reset), wait=at.Delay.AfterExitObex)


elif(args.action == 'putcontact'):
    if(args.file == '-' or args.file == ''):
        raise Exception('Please give a .vcf file for import via --file parameter')
    vcf = open(args.file, 'rb').read()
    vcf = vcf.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n') # ensure CRLF line breaks

    sendAndReadResponse(at.formatCommand(at.Command.EnterObex), wait=at.Delay.AfterEnterObex)

    sendAndReadResponse(
        obex.compileConnect(
            obex.compileMessage(obex.Header.Target, obex.ServiceUuid.DesSync)
        ),
        isObex=True
    )

    sendAndReadResponse(
        obex.compileMessage(
            obex.OpCode.Put+obex.Mask.Final,
            obex.compileNameHeader( obex.FilePath.NewVCardGQS )
            + obex.compileLengthHeader( len(vcf) )
            + obex.compileMessage( obex.Header.EndOfBody, vcf )
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
