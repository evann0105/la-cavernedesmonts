"""Downloadable copies of issued invoices, built only from their frozen snapshot."""
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
from pathlib import Path
from threading import RLock
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from django.utils.dateparse import parse_datetime
from django.core.serializers.json import DjangoJSONEncoder

from .services import DocumentError


_PARIS = ZoneInfo('Europe/Paris')
_FONT_LOCK = RLock()


def _invalid():
    return DocumentError('Cette facture contient des informations incomplètes ou incohérentes. Vérifiez son document avant de l’exporter ; aucune donnée n’a été modifiée.')


def _money(value, places=2):
    if isinstance(value, bool) or value is None:
        raise _invalid()
    try:
        result = Decimal(str(value))
        if (not result.is_finite() or result < 0
                or result != result.quantize(Decimal(1).scaleb(-places))):
            raise _invalid()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise _invalid() from None


def _text(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise _invalid()
    # No control characters are meaningful in an issued printable address.
    return ''.join(char for char in value if char in '\n\t' or ord(char) >= 32).strip()


def _date(value):
    try:
        result = parse_datetime(value) if isinstance(value, str) else value
        if result is None or result.tzinfo is None or result.utcoffset() is None:
            raise _invalid()
        return result
    except (ValueError, TypeError, AttributeError):
        raise _invalid() from None


def _address(value, required=False):
    if not isinstance(value, dict) or not isinstance(value.get('address'), dict):
        if required:
            raise _invalid()
        return ['Non renseignée dans cette commande.']
    address = value['address']
    if required and not all(_text(item) for item in (value.get('name'), address.get('line1'),
            address.get('postal_code'), address.get('city'), address.get('country'))):
        raise _invalid()
    lines = [_text(value.get('name')), _text(address.get('line1')), _text(address.get('line2')),
             ' '.join(filter(None, (_text(address.get('postal_code')), _text(address.get('city'))))),
             _text(address.get('state')), _text(address.get('country')), _text(value.get('email'))]
    return [line for line in lines if line] or ['Non renseignée dans cette commande.']


def validate_snapshot(invoice):
    """Reject damaged data without consulting today's catalogue, profile or order."""
    data = invoice.snapshot
    if not isinstance(data, dict) or not isinstance(invoice.number, str) or not invoice.number.strip():
        raise _invalid()
    if (data.get('number') != invoice.number or data.get('is_demo') is not False
            or data.get('amounts_verified') is not True or data.get('final_ready') is not True
            or data.get('shipping_known') is not True):
        raise _invalid()
    # issue() uses DjangoJSONEncoder, which keeps milliseconds in the frozen
    # JSON while PostgreSQL retains all six microsecond digits in issued_at.
    # Compare against that exact serialized representation, not a tolerance.
    issued_at = _date(invoice.issued_at)
    serialized_issued_at = json.loads(json.dumps(issued_at, cls=DjangoJSONEncoder))
    if _date(data.get('issued_at')) != _date(serialized_issued_at):
        raise _invalid()
    for name in ('ordered_at', 'paid_at'):
        _date(data.get(name))
    if (not _text(data.get('order_reference'))
            or data['order_reference'] != str(getattr(invoice, 'order_id', ''))):
        raise _invalid()
    seller = data.get('seller')
    if not isinstance(seller, dict):
        raise _invalid()
    for key in ('business_name', 'legal_form', 'address', 'siren', 'siret', 'registration', 'vat_notice'):
        if not _text(seller.get(key)):
            raise _invalid()
    _address(data.get('buyer'), required=True)
    mode = data.get('vat_mode')
    if mode not in ('standard', 'franchise'):
        raise _invalid()
    if mode == 'standard':
        rate = _money(data.get('vat_rate'))
        if not 0 < rate <= 100 or not _text(seller.get('vat_number')):
            raise _invalid()
    totals = {key: _money(data.get(key)) for key in (
        'gross', 'discount', 'shipping_amount', 'shipping_ht', 'shipping_vat', 'total', 'tax_base', 'tax_amount')}
    if (totals['gross'] - totals['discount'] + totals['shipping_amount'] != totals['total']
            or totals['tax_base'] + totals['tax_amount'] != totals['total']
            or totals['shipping_ht'] + totals['shipping_vat'] != totals['shipping_amount']):
        raise _invalid()
    if mode == 'franchise' and totals['tax_amount'] != 0:
        raise _invalid()
    lines = data.get('lines')
    if not isinstance(lines, list) or not lines:
        raise _invalid()
    sums = {key: Decimal(0) for key in ('gross', 'discount', 'net', 'ht', 'vat')}
    for line in lines:
        if not isinstance(line, dict) or not _text(line.get('name')):
            raise _invalid()
        quantity = line.get('quantity')
        if type(quantity) is not int or quantity < 1:
            raise _invalid()
        amounts = {key: _money(line.get(key)) for key in ('unit', 'gross', 'discount', 'net', 'ht', 'vat')}
        if mode == 'standard':
            _money(line.get('unit_ht'), 4)
        if (amounts['unit'] * quantity != amounts['gross']
                or amounts['gross'] - amounts['discount'] != amounts['net']
                or amounts['ht'] + amounts['vat'] != amounts['net']):
            raise _invalid()
        for key in sums:
            sums[key] += amounts[key]
    if (sums['gross'] != totals['gross'] or sums['discount'] != totals['discount']
            or sums['net'] + totals['shipping_amount'] != totals['total']
            or sums['ht'] + totals['shipping_ht'] != totals['tax_base']
            or sums['vat'] + totals['shipping_vat'] != totals['tax_amount']):
        raise _invalid()
    return data


def _fonts():
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    with _FONT_LOCK:
        if 'InvoiceVera' not in pdfmetrics.getRegisteredFontNames():
            folder = Path(reportlab.__file__).resolve().parent / 'fonts'
            pdfmetrics.registerFont(TTFont('InvoiceVera', str(folder / 'Vera.ttf')))
            pdfmetrics.registerFont(TTFont('InvoiceVeraBold', str(folder / 'VeraBd.ttf')))
            pdfmetrics.registerFontFamily('InvoiceVera', normal='InvoiceVera', bold='InvoiceVeraBold',
                                         italic='InvoiceVera', boldItalic='InvoiceVeraBold')


def invoice_pdf(invoice):
    """Return a private A4 PDF copy; never issue a number or mutate any record."""
    data = validate_snapshot(invoice)
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, LongTable, TableStyle, KeepTogether

    _fonts()
    green = colors.HexColor('#203f33')
    muted = colors.HexColor('#5f6f63')
    line_color = colors.HexColor('#dbe1d7')
    styles = {
        'body': ParagraphStyle('invoice-body', fontName='InvoiceVera', fontSize=8.2, leading=11.3,
                               textColor=green, spaceAfter=3, splitLongWords=1),
        'small': ParagraphStyle('invoice-small', fontName='InvoiceVera', fontSize=7.2, leading=10,
                                textColor=muted, spaceAfter=3, splitLongWords=1),
        'label': ParagraphStyle('invoice-label', fontName='InvoiceVeraBold', fontSize=8, leading=12,
                                textColor=muted, spaceBefore=8, spaceAfter=5),
        'title': ParagraphStyle('invoice-title', fontName='InvoiceVeraBold', fontSize=23, leading=29,
                                textColor=green, spaceAfter=7),
        'number': ParagraphStyle('invoice-number', fontName='InvoiceVeraBold', fontSize=10, leading=14,
                                 textColor=green, alignment=TA_RIGHT),
        'money': ParagraphStyle('invoice-money', fontName='InvoiceVera', fontSize=8.2, leading=11.3,
                                textColor=green, alignment=TA_RIGHT),
        'tablehead': ParagraphStyle('invoice-tablehead', fontName='InvoiceVeraBold', fontSize=7, leading=10,
                                    textColor=colors.white),
    }

    def p(value, style='body'):
        return Paragraph(escape(_text(value)).replace('\n', '<br/>'), styles[style])

    def amount(value, places=2):
        return f'{_money(value, places):,.{places}f}'.replace(',', ' ').replace('.', ',') + ' €'

    def printed_date(value):
        return _date(value).astimezone(_PARIS).strftime('%d/%m/%Y à %H:%M')

    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm,
        topMargin=17*mm, bottomMargin=20*mm, title='Facture ' + invoice.number,
        author=_text(data['seller']['business_name']), subject='Copie de la facture émise',
        pageCompression=1)
    width = A4[0] - 36*mm
    story = []
    heading = Table([[p('Facture', 'title'), p(invoice.number, 'number')],
                     [p('ACQUITTÉE', 'small'), p('Émise le ' + printed_date(data['issued_at']), 'small')]],
                    colWidths=[width*.46, width*.54])
    heading.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(-1,-1),0),
        ('RIGHTPADDING',(0,0),(-1,-1),0), ('BOTTOMPADDING',(0,0),(-1,-1),3),
        ('LINEBELOW',(0,-1),(-1,-1),.7,green)]))
    story.extend([heading, Spacer(1,6*mm)])
    seller = data['seller']
    from espace.legal import short_legal_form
    seller_lines = [f"{_text(seller['business_name'])} · {short_legal_form(_text(seller['legal_form']))}",
        _text(seller['address']), 'SIREN ' + _text(seller['siren']), 'SIRET ' + _text(seller['siret']),
        _text(seller['registration'])]
    if seller.get('vat_number'):
        seller_lines.append('N° TVA : ' + _text(seller['vat_number']))
    seller_lines.extend(filter(None, (_text(seller.get('email')), _text(seller.get('phone')))))
    parties = Table([[[p('VENDEUR','label'), p('\n'.join(seller_lines))],
                     [p('FACTURATION','label'), p('\n'.join(_address(data['buyer'], required=True))),
                      p('LIVRAISON','label'), p('\n'.join(_address(data.get('shipping'))))]]],
                    colWidths=[width*.5, width*.5])
    parties.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(-1,-1),0),
                                ('RIGHTPADDING',(0,0),(-1,-1),12), ('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.extend([parties, Spacer(1,3*mm)])
    references = [('Référence de commande', data['order_reference']),
                  ('Commande passée le', printed_date(data['ordered_at'])),
                  ('Paiement confirmé le', printed_date(data['paid_at'])),
                  ('Régime de TVA', 'TVA ' + _text(data.get('vat_rate')) + ' %' if data['vat_mode']=='standard' else 'Franchise en base')]
    reference_table = Table([[p(label,'small'),p(value)] for label,value in references], colWidths=[width*.32,width*.68])
    reference_table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),
        ('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#f0f3ec')),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
    story.extend([reference_table,Spacer(1,7*mm)])
    taxed = data['vat_mode']=='standard'
    headers=['Désignation','Qté','P.U. TTC','Remise TTC','Total TTC']+(['Total HT'] if taxed else [])
    widths=([width*.39,width*.06,width*.14,width*.14,width*.14,width*.13] if taxed
            else [width*.44,width*.07,width*.16,width*.16,width*.17])
    cells=[[p(label,'tablehead') for label in headers]]
    for item in data['lines']:
        label = _text(item['name'])
        variant = ' · '.join(filter(None, (_text(item.get('size')), _text(item.get('color')))))
        if variant:
            label += '\n' + variant
        if taxed:
            label += '\nP.U. HT : ' + amount(item['unit_ht'],4) + ' · TVA ' + data['vat_rate'] + ' %'
        cells.append([p(label),p(str(item['quantity']),'money'),p(amount(item['unit']),'money'),
            p(amount(item['discount']),'money'),p(amount(item['net']),'money')]
            + ([p(amount(item['ht']),'money')] if taxed else []))
    line_table = LongTable(cells,colWidths=widths,repeatRows=1,hAlign='LEFT',splitByRow=1,splitInRow=1)
    line_table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),green),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
        ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('LINEBELOW',(0,1),(-1,-1),.35,line_color)]))
    story.extend([line_table,Spacer(1,6*mm)])
    totals = [('Articles avant remises', data['gross']),('Remises sur les articles', '-' + amount(data['discount'])),
              ('Livraison' + (' · TVA ' + data['vat_rate'] + ' %' if taxed else ''),data['shipping_amount']),
              ('Total HT',data['tax_base']),('TVA ' + data['vat_rate'] + ' %' if taxed else 'TVA non applicable',data['tax_amount']),
              ('Total payé',data['total'])]
    total_cells=[[p(label), p(value if index==1 else amount(value),'money')] for index,(label,value) in enumerate(totals)]
    total_table=Table(total_cells,colWidths=[width*.43,width*.21],hAlign='RIGHT')
    total_table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),10),
        ('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LINEABOVE',(0,-1),(-1,-1),.8,green),('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#eaf0e2'))]))
    story.extend([KeepTogether([total_table]),Spacer(1,7*mm)])
    story.append(p('Facture acquittée. Commande réglée via le parcours de paiement en ligne. Aucun nouveau règlement n’est demandé.','small'))
    story.append(p(seller['vat_notice'],'small'))
    if data.get('document_note'):
        story.append(p(data['document_note'],'small'))
    contact = ' · '.join(filter(None,(_text(seller.get('email')),_text(seller.get('phone')))))
    if contact:
        story.append(p('Pour une commande, un retour ou une correction : '+contact,'small'))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(line_color)
        canvas.line(18*mm,15*mm,A4[0]-18*mm,15*mm)
        canvas.setFont('InvoiceVera',7)
        canvas.setFillColor(muted)
        canvas.drawString(18*mm,10*mm,'Document privé · Facture '+invoice.number)
        canvas.drawRightString(A4[0]-18*mm,10*mm,'Page '+str(doc.page))
        canvas.restoreState()
    try:
        document.build(story,onFirstPage=footer,onLaterPages=footer)
    except Exception as error:
        # Do not return a partly generated invoice or leak private data in errors.
        raise DocumentError('La copie PDF de cette facture n’a pas pu être préparée. Vérifiez le document avant de réessayer.') from error
    return buffer.getvalue()
