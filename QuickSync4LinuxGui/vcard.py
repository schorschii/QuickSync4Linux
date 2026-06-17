#!/usr/bin/env python3

import quopri
import re


CARD_RE = re.compile(r"BEGIN:VCARD[\S\s]*?END:VCARD", re.IGNORECASE)


def _decode_value(value, params):
    if params.get('ENCODING', '').upper() == 'QUOTED-PRINTABLE':
        charset = params.get('CHARSET', 'UTF-8')
        try:
            return quopri.decodestring(value.encode('ascii', errors='replace')).decode(charset, errors='replace')
        except Exception:
            return value
    return value


def _qp_encode(text):
    encoded = quopri.encodestring(text.encode('utf-8'), quotetabs=False).decode('ascii')
    return encoded.replace('\n', '').replace('\r', '')


def _needs_qp(text):
    return any(ord(c) > 127 or c == ';' for c in text)


def _split_n(value):
    parts = value.split(';')
    while len(parts) < 5:
        parts.append('')
    return {
        'last_name': parts[0],
        'first_name': parts[1],
        'middle_name': parts[2],
        'prefix': parts[3],
        'suffix': parts[4],
    }


def _join_n(card):
    return ';'.join([
        card.get('last_name', ''),
        card.get('first_name', ''),
        card.get('middle_name', ''),
        card.get('prefix', ''),
        card.get('suffix', ''),
    ])


def _split_adr(value):
    parts = value.split(';')
    while len(parts) < 7:
        parts.append('')
    return {
        'pobox': parts[0],
        'ext': parts[1],
        'street': parts[2],
        'city': parts[3],
        'region': parts[4],
        'zip': parts[5],
        'country': parts[6],
    }


def _join_adr(adr):
    return ';'.join([
        adr.get('pobox', ''),
        adr.get('ext', ''),
        adr.get('street', ''),
        adr.get('city', ''),
        adr.get('region', ''),
        adr.get('zip', ''),
        adr.get('country', ''),
    ])


def parseLines(card_text):
    """Yield (name, params_dict, value) tuples for the VCARD body."""
    raw = card_text.replace('\r\n', '\n').split('\n')

    merged = []
    i = 0
    while i < len(raw):
        line = raw[i]
        upper = line.upper()
        is_qp = 'QUOTED-PRINTABLE' in upper
        while is_qp and line.endswith('=') and i + 1 < len(raw):
            i += 1
            line = line[:-1] + raw[i]
        merged.append(line)
        i += 1

    for line in merged:
        if ':' not in line:
            continue
        prefix, value = line.split(':', 1)
        head = prefix.split(';')
        name = head[0].strip().upper()
        if name in ('BEGIN', 'END', 'VERSION'):
            continue
        params = {}
        types = []
        for p in head[1:]:
            if '=' in p:
                k, v = p.split('=', 1)
                params[k.strip().upper()] = v.strip()
            else:
                types.append(p.strip().upper())
        if types:
            params['TYPE'] = types
        yield name, params, _decode_value(value, params)


def parseCards(vcf_text):
    """Parse a full VCF blob, return list of contact dicts."""
    out = []
    for raw_card in CARD_RE.findall(vcf_text):
        card = {
            'luid': None,
            'last_name': '', 'first_name': '', 'middle_name': '', 'prefix': '', 'suffix': '',
            'nickname': '',
            'org': '',
            'title': '',
            'bday': '',
            'note': '',
            'tels': {'HOME': '', 'CELL': '', 'WORK': '', 'FAX': '', 'OTHER': ''},
            'emails': {'HOME': '', 'WORK': '', 'OTHER': ''},
            'addresses': {'HOME': _split_adr(''), 'WORK': _split_adr('')},
            'url': '',
            'extras': [],
        }
        for name, params, value in parseLines(raw_card):
            types = params.get('TYPE', [])
            if name == 'X-IRMC-LUID':
                card['luid'] = value
            elif name == 'N':
                card.update(_split_n(value))
            elif name == 'FN':
                if not card['first_name'] and not card['last_name']:
                    card['first_name'] = value
            elif name == 'NICKNAME':
                card['nickname'] = value
            elif name == 'ORG':
                card['org'] = value
            elif name == 'TITLE':
                card['title'] = value
            elif name == 'BDAY':
                card['bday'] = value
            elif name == 'NOTE':
                card['note'] = value
            elif name == 'URL':
                card['url'] = value
            elif name == 'TEL':
                key = next((t for t in ('HOME', 'CELL', 'WORK', 'FAX') if t in types), 'OTHER')
                card['tels'][key] = value
            elif name == 'EMAIL':
                key = next((t for t in ('HOME', 'WORK') if t in types), 'OTHER')
                card['emails'][key] = value
            elif name == 'ADR':
                key = 'WORK' if 'WORK' in types else 'HOME'
                card['addresses'][key] = _split_adr(value)
            else:
                card['extras'].append((name, params, value))
        out.append(card)
    return out


def _emit(name, value, force_qp=False):
    if force_qp or _needs_qp(value):
        return f'{name};ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:{_qp_encode(value)}'
    return f'{name}:{value}'


def formatCard(card):
    """Serialize a contact dict to a VCF 2.1 VCARD block (CRLF line endings)."""
    lines = ['BEGIN:VCARD', 'VERSION:2.1']
    if card.get('luid'):
        lines.append(f'X-IRMC-LUID:{card["luid"]}')

    n_value = _join_n(card)
    if any(ord(c) > 127 for c in n_value):
        lines.append(f'N;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:{_qp_encode(n_value)}')
    else:
        lines.append(f'N:{n_value}')

    full_name = (card.get('first_name', '') + ' ' + card.get('last_name', '')).strip()
    if full_name:
        if any(ord(c) > 127 for c in full_name):
            lines.append(f'FN;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:{_qp_encode(full_name)}')
        else:
            lines.append(f'FN:{full_name}')

    if card.get('nickname'):
        lines.append(_emit('NICKNAME', card['nickname']))
    if card.get('org'):
        lines.append(_emit('ORG', card['org']))
    if card.get('title'):
        lines.append(_emit('TITLE', card['title']))

    for kind in ('HOME', 'CELL', 'WORK', 'FAX', 'OTHER'):
        value = card.get('tels', {}).get(kind, '').strip()
        if value:
            lines.append(f'TEL;{kind}:{value}' if kind != 'OTHER' else f'TEL:{value}')

    for kind in ('HOME', 'WORK', 'OTHER'):
        value = card.get('emails', {}).get(kind, '').strip()
        if value:
            lines.append(f'EMAIL;{kind}:{value}' if kind != 'OTHER' else f'EMAIL:{value}')

    for kind in ('HOME', 'WORK'):
        adr = card.get('addresses', {}).get(kind, {})
        adr_value = _join_adr(adr) if adr else ''
        if adr_value.strip(';'):
            if any(ord(c) > 127 for c in adr_value):
                lines.append(f'ADR;{kind};ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:{_qp_encode(adr_value)}')
            else:
                lines.append(f'ADR;{kind}:{adr_value}')

    if card.get('bday'):
        lines.append(f'BDAY:{card["bday"]}')
    if card.get('url'):
        lines.append(_emit('URL', card['url']))
    if card.get('note'):
        lines.append(_emit('NOTE', card['note']))

    for name, params, value in card.get('extras', []):
        param_str = ''
        for k, v in params.items():
            if k == 'TYPE':
                for t in v:
                    param_str += f';{t}'
            else:
                param_str += f';{k}={v}'
        if any(ord(c) > 127 for c in value) and 'ENCODING' not in params:
            lines.append(f'{name}{param_str};ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:{_qp_encode(value)}')
        else:
            lines.append(f'{name}{param_str}:{value}')

    lines.append('END:VCARD')
    return '\r\n'.join(lines) + '\r\n'


def displayName(card):
    parts = [card.get('first_name', ''), card.get('last_name', '')]
    name = ' '.join(p for p in parts if p).strip()
    return name or card.get('org', '') or '(unbenannt)'
