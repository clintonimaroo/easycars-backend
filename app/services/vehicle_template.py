from html import escape

ICON_BASE = "https://dashboard.easycarsllc.com/images/product-details/v1/"
PHONE_LINE = "For more information or to schedule a test drive, please call +1 (214)989 4804."

SPECS = (
    ("vin", "VIN", "vin"),
    ("mileage", "Mileage", "mileage"),
    ("drivetrain", "Drivetrain", "drivetrain"),
    ("exterior", "Exterior color", "color"),
    ("interior", "Interior color", "interior"),
    ("mpg", "MPG", "fuel"),
    ("engine", "Engine", "engine"),
    ("fuel", "Fuel type", "fuel"),
    ("transmission", "Transmission", "transmission"),
)

FEATURE_GROUPS = (
    ("safety", "Safety & Security", "safety"),
    ("wheels", "Wheels & Tires", "wheels"),
    ("braking", "Braking & Traction", "braking"),
    ("seating", "Seating & Interior", "interior"),
    ("entertainment", "Entertainment & Technology", "entertainment"),
    ("comfort", "Comfort & Convenience", "comfort"),
    ("lighting", "Lighting", "lighting"),
)

SHORT_DESCRIPTION = (
    '<strong>Book a Test Drive</strong>\n\n[contact-form to="slizzyobeto@gmail.com" subject="Someone booked a test drive "]'
    '[contact-field label="Full Name" type="name" required="1"][contact-field label="Phone" type="text" required="1"]'
    '[contact-field label="Email" type="email" required="1"][contact-field label="Message" type="textarea"][/contact-form]'
)


def _icon(name, size=28):
    return (
        f'<img src="{ICON_BASE}{name}.png" alt="" width="{size}" height="{size}" '
        f'style="display:block;width:{size}px;height:{size}px;flex-shrink:0;object-fit:contain;" />'
    )


def _clean(value):
    return " ".join(str(value).split()) if value is not None else ""


def _lines(value):
    if isinstance(value, str):
        value = value.splitlines()
    return [_clean(line) for line in value or [] if _clean(line)]


def _spec(key, label, symbol, value):
    return (
        '<div style="display:flex;flex:1 1 175px;min-width:0;gap:12px;align-items:flex-start;">\n'
        f'{_icon(symbol)}<div style="min-width:0;"><p style="margin:0 0 4px;font-size:12px;line-height:1.4;opacity:0.7;">{label}</p>'
        f'<p data-easycars-field="{key}" style="margin:0;font-size:15px;line-height:1.55;font-weight:600;">{escape(value) if value else "-"}</p></div></div>'
    )


def _group(key, label, symbol, items):
    bullets = "".join(f"<li>{escape(item)}</li>" for item in items) or "<li>-</li>"
    return (
        '<div style="flex:1 1 250px;min-width:0;">\n'
        f'<div style="display:flex;gap:10px;align-items:center;margin:0 0 12px;">{_icon(symbol, 24)}'
        f'<h3 style="margin:0;font-size:17px;line-height:1.4;font-weight:600;">{escape(label)}</h3></div>\n'
        f'<ul data-easycars-field="{key}" style="margin:0;padding:0 0 0 20px;font-size:14px;line-height:1.8;">{bullets}</ul></div>'
    )


def render_vehicle_description(name, specs=None, features=None, overview=None):
    specs = specs or {}
    features = features or {}
    title = _clean(name) or "Vehicle details"
    spec_html = "\n".join(
        _spec(key, label, symbol, _clean(specs.get(key)))
        for key, label, symbol in SPECS
        if key != "vin" or _clean(specs.get("vin"))
    )
    feature_html = "\n".join(_group(key, label, symbol, _lines(features.get(key))) for key, label, symbol in FEATURE_GROUPS)
    paragraphs = _lines(overview) or ["-"]
    overview_html = "".join(f'<p style="margin:0 0 14px;">{escape(line)}</p>' for line in paragraphs)
    return (
        '<div class="easycars-vehicle-template" data-easycars-template="1" style="font-family:inherit;color:inherit;line-height:1.65;max-width:100%;">\n'
        '<p style="margin:0 0 8px;color:#b8860b;font-size:11px;font-weight:700;letter-spacing:1.5px;">VEHICLE OVERVIEW</p>\n'
        f'<h2 data-easycars-title="1" style="margin:0 0 28px;font-size:24px;line-height:1.3;font-weight:700;">{escape(title)}</h2>\n'
        f'<div style="display:flex;flex-wrap:wrap;gap:24px 28px;margin:0 0 36px;">{spec_html}</div>\n'
        '<h2 style="margin:0 0 22px;font-size:21px;line-height:1.35;font-weight:650;">Features & equipment</h2>\n'
        f'<div style="display:flex;flex-wrap:wrap;gap:28px 32px;margin:0 0 36px;">{feature_html}</div>\n'
        '<h2 style="margin:0 0 12px;font-size:21px;line-height:1.35;font-weight:650;">Description</h2>\n'
        f'<div data-easycars-field="overview" style="font-size:15px;line-height:1.8;">{overview_html}</div>\n'
        f'<p style="margin:24px 0 0;font-size:14px;line-height:1.7;">{PHONE_LINE}</p>\n'
        "</div>"
    )
