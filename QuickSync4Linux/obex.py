#!/usr/bin/env python3

from xml.dom import minidom, expatbuilder
from enum import Enum
import struct

# https://en.wikipedia.org/wiki/OBject_EXchange
# https://btprodspecificationrefs.blob.core.windows.net/ext-ref/IrDA/OBEX15.pdf

class Connection:
    Version = b"\x10"
    Flags   = b"\x00"
    MaxPacketSize = b"\xff\xfe"

class SetPathFlags:
    LayerUp    = 0b00000001 # backup a level before applying name (equivalent to ../)
    DontCreate = 0b00000010 # don't create folder if it does not exist
    Constants  = 0x00 # reserved, always 0x00

class Mask:
    # the final bit indicates the last packet for the request/response
    Final    = 0b10000000
    NotFinal = 0b01111111

class OpCode:
    Connect    = 0x80
    Disconnect = 0x81
    Put        = 0x02
    Get        = 0x03
    SetPath    = 0x85
    Session    = 0x87
    Abort      = 0xff

class ReCode(int, Enum):
    # HTTP 1xx codes
    Continue         = 0x10

    # HTTP 2xx codes
    Success          = 0x20
    Created          = 0x21
    Accepted         = 0x22
    NonAuthoritative = 0x23
    NoContent        = 0x24
    ResetContent     = 0x25
    PartialContent   = 0x26

    # HTTP 3xx codes
    MultipleChoices  = 0x30
    MovedPermanently = 0x31
    MovedTemporarily = 0x32
    SeeOther         = 0x33
    NotModified      = 0x34
    UseProxy         = 0x35

    # HTTP 4xx codes
    BadRequest         = 0x40
    Unauthorized       = 0x41
    PaymentRequired    = 0x42
    Forbidden          = 0x43
    NotFound           = 0x44
    MethodNotAllowed   = 0x45
    NotAcceptable      = 0x46
    ProxyAuthRequired  = 0x47
    RequestTimeOut     = 0x48
    Conflict           = 0x49
    Gone               = 0x4a
    LengthRequired     = 0x4b
    PreconditionFail   = 0x4c
    ReqEntityTooLarge  = 0x4d
    RequestUrlTooLarge = 0x4e
    UnsupportedMedia   = 0x4f

    # HTTP 5xx codes
    InternalServerError = 0x50
    NotImplemented      = 0x51
    BadGateway          = 0x52
    ServiceUnavailable  = 0x53
    GatewayTimeout      = 0x54
    VersionNotSupported = 0x55

    # special codes
    DatabaseFull        = 0x60
    DatabaseLocked      = 0x61

class Header:
    Count         = 0xc0
    Name          = 0x01
    Type          = 0x42
    Length        = 0xc3
    TimeIso8601   = 0x44
    Time4byte     = 0xc4
    Description   = 0x05
    Target        = 0x46
    HTTP          = 0x47
    Body          = 0x48
    EndOfBody     = 0x49
    Who           = 0x4a
    ConnectionId  = 0xcb
    AppParameters = 0x4c
    AuthChallenge = 0x4d
    AuthResponse  = 0x4e
    CreatorId     = 0xcf
    WanUuid       = 0x50
    ObjectClass   = 0x51
    SessionParams = 0x52
    SessionSeqNo  = 0x93
    ActionId      = 0x94
    DestName      = 0x15
    Permissions   = 0xd6
    SingleResponseMode   = 0x97
    SingleResponseParams = 0x98
    # 0x19 to 0x2f = Reserved
    # 0x30 to 0x3f = User defined

class AppParametersCommand:
    MemoryStatusTotal = b"\x32\x01\x01"
    MemoryStatusFree  = b"\x32\x01\x02"

class ServiceUuid:
    IrMcSync       = b"\x49\x52\x4d\x43\x2d\x53\x59\x4e\x43"
    DesSync        = b"\x6b\x01\xcb\x31\x41\x06\x11\xd4\x9a\x77\x00\x50\xda\x3f\x47\x1f"
    FolderBrowsing = b"\xf9\xec\x7b\xc4\x95\x3c\x11\xd2\x98\x4e\x52\x54\x00\xdc\x9e\x09"
    SyncMl         = b"\x53\x59\x4e\x43\x4d\x4c\x2d\x53\x59\x4e\x43"

class FolderPath:
    ClipPictures = "/Clip Pictures"
    ScreenSavers = "/Pictures"
    Ringtones    = "/Sounds"

class FilePath:
    PhoneBook     = "/telecom/pb.vcf"
    InfoLog       = "/telecom/pb/info.log"
    DevInfo       = "/telecom/devinfo.txt"
    LuidCC        = "/telecom/pb/luid/cc.log"
    Luid0         = "/telecom/pb/luid/0.log"
    VCardLuid     = "/telecom/pb/luid/{0}.vcf"
    NewVCardGQS   = "/telecom/pb/luid/zapis.vcf"
    NewVCardGDS   = "/telecom/pb/luid/.vcf"
    IncomingCalls = "telecom/ich.log"
    OutgoingCalls = "telecom/och.log"
    MissedCalls   = "telecom/mch.log"

class ObjectMimeType:
    FolderListing = "x-obex/folder-listing\0"
    Capability    = "x-obex/capability\0"

class QuickSyncOperation:
    Download = 1
    Upload   = 2

class ObexException(Exception):
    pass
class InvalidObexLengthException(Exception):
    pass

def compileMessage(opcode, payload=b''):
    if(not isinstance(payload, (bytes, bytearray))):
        payload = payload.encode('ascii')
    return struct.pack('B', opcode) + struct.pack('>H', len(payload)+3) + payload

def compileConnect(optionalHeaders):
    return compileMessage(
        OpCode.Connect,
        Connection.Version + Connection.Flags + Connection.MaxPacketSize + optionalHeaders
    )

def compileNameHeader(text):
    return compileMessage(Header.Name, b'\x00'+text.encode('utf-16-le')+b'\x00')

def compileLengthHeader(length):
    return struct.pack('B', Header.Length) + struct.pack('>I', length)

def parseMemoryResponse(data, offset=1):
    if(data[offset] == 1):
        return data[offset + 1]
    elif(data[offset] == 2):
        return struct.unpack('>H', data[offset+1:])[0]
    elif(data[offset] == 4):
        return struct.unpack('>I', data[offset+1:])[0]
    else: return 0

def evaluateResponse(buf, results, ser, isUpload):
    if(len(buf) < 3
    or len(buf) != struct.unpack('>H', buf[1:3])[0]):
        raise InvalidObexLengthException()

    elif(buf[0] & Mask.NotFinal == ReCode.Continue and buf[0] & Mask.Final):
        if(isUpload):
            return True
        else:
            results.extend(parseHeaders(buf[3:]))
            ser.write(compileMessage(OpCode.Get+Mask.Final))
            return False

    elif(buf[0] & Mask.NotFinal == ReCode.Success):
        results.extend(parseHeaders(buf[3:]))
        return True

    else:
        errorString = 'Unknown Error'
        try:
            errorString = str(ReCode(buf[0] & Mask.NotFinal))
        except ValueError: pass
        raise ObexException('Device reported an obex command error, code {:02X} ({})'.format(buf[0], errorString))

def parseHeaders(obj):
    results = []

    if(len(obj) < 3):
        return results

    currOffset = 0
    while True:
        currLength = 1

        if(len(obj) <= currOffset): break

        if(obj[currOffset] == Header.Length):
            currLength = 5
            print('Payload Length:', struct.unpack('>I', obj[currOffset+1:currOffset+5])[0])

        elif(obj[currOffset] == Header.Count):
            currLength = 5
            print('Payload Count:', struct.unpack('>I', obj[currOffset+1:currOffset+5])[0])

        elif(obj[currOffset] == Header.Body
        or obj[currOffset] == Header.EndOfBody
        or obj[currOffset] == Header.AppParameters):
            if(len(obj) < currOffset+3): break
            currLength = struct.unpack('>H', obj[currOffset+1:currOffset+3])[0]
            if(currLength == 0): break

            currHeader = obj[currOffset:currOffset+currLength]
            if(len(currHeader) != currLength):
                raise InvalidObexLengthException()

            results.append(currHeader[3:])

        else:
            #print('Unknown Header:', struct.pack('B', obj[currOffset]))
            break

        currOffset += currLength

    return results

def parseFileListXml(xmlstring):
    files = []
    maxLenName = 0
    document = minidom.parseString(xmlstring).documentElement
    for file in document.getElementsByTagName('file'):
        maxLenName = max(maxLenName, len(file.getAttribute('name')))
        files.append({
            'name': file.getAttribute('name'),
            'size': file.getAttribute('size'),
            'fileid': file.getAttribute('fileid'),
            'modified': file.getAttribute('modified'),
            'user-perm': file.getAttribute('user-perm'),
            'group-perm': file.getAttribute('group-perm'),
        })
    return files, maxLenName
