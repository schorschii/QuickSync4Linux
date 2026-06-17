#!/usr/bin/env python3

# really horrible: without proper delay, commands are not working or we simply get no response...
class Delay:
    AfterInvoke    : int = 0.100
    AfterEnterObex : int = 0.500
    AfterExitObex  : int = 0.500
    TimeoutRead    : int = 2.000
    TimeoutWrite   : int = 2.000
    ObexBoundary   : int = 1.000

# AT commands recognized by the Gigaset devices
class Command:
    GetHardwareConnectionState = "AT^SGST\r\n"
    GetFirmwareUrl = "AT^SURL\r\n"
    GetAreaCodes = "AT^SACO?\r\n"
    SetAreaCodes = "AT^SACO={0},{1},{2},{3}\r\n"
    SwitchHandsFree = "ATC\r\n"
    Dial = "ATD {0}\r\n"
    DialInternal = "ATDI {0}\r\n"
    Answer = "ATA\r\n"
    HangUp = "ATH\r\n"
    Ping = "AT\r\n"
    Reset = "ATZ\r\n\r\n"
    GetDeviceType = "AT+CGMM\r\n"
    GetManufacturer = "AT+CGMI\r\n"
    GetSerialNumber = "AT+CGSN\r\n"
    GetFirmwareVersion = "AT+CGMR\r\n"
    GetProductName = "AT^WPPN\r\n"
    GetSupportedFeatures = "AT^LOSF=?\r\n"
    GetCharset1 = "AT^WPCS\r\n"
    GetCharset2 = "AT^WPCS?\r\n"
    GetExtendedMessageLevels = "AT+CMEE=?\r\n"
    GetCurrentMessageLevel = "AT+CMEE?\r\n"
    SetExtendedMessageLevel = "AT+CMEE={0}\r\n"
    GetMWI = "AT^HMWI?\r\n"
    GetSupportedMultimedia = "AT^HSMM?\r\n"
    GetScreenSizeClip = "AT^WPPS CLIP\r\n"
    GetScreenSizeFull = "AT^WPPS SCR\r\n"
    GetBatteryState = "AT+CBC\r\n"
    GetSignalState = "AT+CSQ\r\n"
    GetInternalName = "AT^SHSN?\r\n"
    GetExtendedModesList = "AT^SQWE=?\r\n"
    GetCurrentExtendedMode = "AT^SQWE?\r\n"
    PrepareHsImageXml = "AT^DMPC=?\r\n"
    RequestPartHsImageXml = "AT^DMPC?\r\n"
    AnswerPartHsImageXml = "^DMPC:\r\n"
    EnterObex = "AT^SQWE=3\r\n"
    EnterMemoryDump = "AT^SQWE=55\r\n"
    ExitObex = "+++"
    SwitchRoleGeneric = "AT^SRSR {0}\r\n"
    SwitchRoleDefault = "AT^SRSR 0\r\n"
    SwitchRoleQUC = "AT^SRSR 1\r\n"
    SwitchRoleUpdate = "AT^SRSR 2\r\n"
    ListMelodies = "AT^RM=?\r\n"
    InitializeImageUpload = "AT^DMPU=?\r\n"
    SendBasicImageInfo = "AT^DMPU={0},{1},{2}\r\n"
    GetImageUploadResult = "AT^DMPU?\r\n"
    SendImagePart = "AT^DMPW={0},{1},{2},{3}\r\n"

class AtException(Exception):
    pass
class IncompleteAtResponseException(Exception):
    pass

def removePrefix(s, pre):
    if pre and s.startswith(pre):
        return s[len(pre):]
    return s
def removeSuffix(s, suf):
    if suf and s.endswith(suf):
        return s[:-len(suf)]
    return s

def formatCommand(cmd, *args):
        return cmd.format(*args).encode('ascii')

def evaluateResponse(buf, request):
    if(request.decode('ascii') == Command.ExitObex):
        # special handling for the ExitObex command which does not return any text...
        return True

    elif(request.decode('ascii').startswith(Command.Dial.format('').strip()) and b'OK\r\n' in buf):
        return True

    elif(buf.endswith(b'OK\r\n')):
        return removeSuffix(removePrefix(buf, request.strip()), b'OK\r\n').strip()

    elif(buf.endswith(b'ERROR\r\n')):
        raise AtException('Device reported an AT command error')

    else:
        raise IncompleteAtResponseException()
