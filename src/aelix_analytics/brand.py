"""The Aelix mark: the browser-tab icon of every page the kit builds, and the small mark beside it.

The image is the Aelix brand's own 48-pixel raster of its mark ("Forged Planes"): the 48 x 48 entry of
aelix-ai `docs/assets/brand/favicon.ico` (commit 651f71e), which the brand work rendered from
`mark.svg`. Its bytes are kept as they are (sha256 05cc7992baef6b13...; IHDR, IDAT and IEND only, no
text or time chunk). The brand rules say not to recolour, stretch, outline or re-space it; it has a
transparent ground made for light and dark pages, and 48 pixels keep it sharp up to 20 CSS pixels on
a screen of double density and 16 on one of triple.

A PNG rather than the SVG: the SVG's strand is a gradient named by a fragment (`url(#aelix-strand)`),
and a shared copy's package check resolves a fragment inside a `data:` document against the page it
is in, so it refuses that page. A PNG names nothing, and every browser takes it as a tab icon.

It is a `data:` URI, so a page opened from a folder, a ZIP or another reader's synced copy shows it
with no other file, and a shared copy's page policy (`img-src 'self' data:`) already allows it. A
page that allows no image at all (a Report's body document) does not carry it.

Standard library only, and nothing of the kit: the shared-copy renderer may import nothing but its
model, and this module is the one thing it shares with the Workspace.
"""

from __future__ import annotations

MARK_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAAEqUlEQVR4nO1ZwU8cVRj/fTO7LCXOCkjViLQcJNESTXBNiInG"
    "xIvX5SDh0HtPUChab8Rwq4Lgrhf+AlN66Dbx4skYG1sOq2lTKlZMaNdi2lIIbF3DsvN+HmZmZwg7sDM7VQ/7O307b95+v9/7"
    "vrzve2+AJppooon/EhJ0Qtf5C0ZZJZYB9Pi8Mr8zMz5Ra6D3xqM5QBsXj1dxfxTicbP/11ePF4PwCSwAAIzJubSIXPYZNoUy"
    "uD17Nu99eOLmo5SmZAkC3XIsVe8iAqEM/f5GRy4oFy3oBAAozk7kRODnTKdwAR8u6tUni9RFYYGgThIAQBCODSIXhjwQUgAA"
    "6KY5BsAv3Knkyfujzo8TfRujJFNVviQ8dlGEY2F5hEohB8mP5sYBmfMZLsaU2d86MoKYHlumiOGk+770oTaxNvDcfFgOoSMA"
    "ADt3u7MA8j7DhqnrGU2PZQgYgL3qdNIHAJBf+60z2wiHhgTg0rAplDMAzFrDevfLaZBplzA95GnSxBkMS8259aIxAQDs3ebg"
    "KsbiaB0YAAGXPD02kL2XOu4XvbrRsAAAaNF2pwAUvM8Sr/cDbW1w8oZVJQQphb//kqkofEciYOOzT4qku5NoHR2IvdIHkG4E"
    "PLaQYxvvBCtYfohEAOCpDSJoSaUAwCZMD3lCgFzhrRdyUfmNTABg1Qa9r29Xa++AlepO0bJtVnaVMkPv+bUQi/LPWkdGYFYq"
    "IAghQBHAWnVQBOUbt8BfbkfpMtoIVJTKULSEN2Uc29zcxN6dOwlT1zNR+oxMwPM/3ktTMW0VKoe8nT5KoZzPW2KItDE5l47K"
    "byQCuq6uGCQzTpVFlbxl762uQm1tVd8XkUzX+QtGFL4jisCxaRI9hLvqjq1KJewtH8j7nrJKTEfhuaFmDgC6fribonAJgL6v"
    "SbPtyvXrMNfXa02teW4IisYisEhdQS2Q0N2Ugbdhy6k/13M+sw+eG0KgIQHtL66NgkhZO42dPq5dVBV9LMi5IQxCC+j8frVH"
    "yGna22R11W1bQU1tvtdd2PxisgDwkL5HpjvPzfqdr49EaAGKkiFpOFXWTR+CYH5zvbfaodZzbgjLI5SA9u9W0wTSAKqFyk0f"
    "mBq0/X3+EeeGRmpDYAFdV1cMBWSsouSQd22hym68e/LAavueG2yErQ2Bt9Hk51//hAcPBoLOq4+M/Lw9c/bNIHMCRSCZvXIa"
    "Dx8+FfIAQHAgeW7+dJA59Ufg0+WWJK5t4cmTtsDMgkBQ2ll7qQOXhsv1vF53BAzj1sWnTh4AiDaj9/7Fel+vKwLJr3KDLPxx"
    "TcxKw61HfRDGUX778czHS0e9WVcEuL19+d8jDwCUPcT97l734UhSbd/eThPWRa51syZwezYxqavB0vuvhWrInp38stoI1hon"
    "OVScncgd9h+HR+DKiqGIjL3b222+0/MAALNhyQPR1IZDBSQSlWmAPU6VhadhA1A4VkLDdzu17pQ8OPLc4JtC8W9upjRdr4bX"
    "8yHCus8Hh0ofnMoFZlwDYb43OKgdgcVFXXR9gfTc57Paa0KRuajIAyG+N3hQU0DimVOjIFPOb8+HCJAsahLt3Q4Q7HtDE000"
    "0cT/B/8A5w130zsdQ54AAAAASUVORK5CYII="
)

MARK_URI = "data:image/png;base64," + MARK_PNG_BASE64

# The browser-tab icon. One link, the same bytes on every page.
ICON_LINK = f'<link rel="icon" type="image/png" sizes="48x48" href="{MARK_URI}">'


def mark(size: int = 16) -> str:
    """The mark as a decorative image beside words that already name the product: a page's footer
    (the owner chose the footer over the top bar and over the tab icon alone, #204, 2026-11-20)."""
    return f'<img class="mark" src="{MARK_URI}" alt="" width="{size}" height="{size}">'
