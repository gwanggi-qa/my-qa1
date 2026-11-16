"""Build-11 word feedback: visible correct/wrong icons, never named grades."""
import hashlib
import io

from speech_navigation import NavigationBlocked, Screen, _bounds

APK_SHA256 = "b7ee56ed321fc25edc252f7e428e7371a658d4d75cad171bd1473eacae5f3ea0"
OUTCOMES = {"word_correct", "word_wrong"}


def observe_word_png(png, templates):
    """Locate one build-bound icon in a target-checkpointed PNG, without late XML."""
    import cv2
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(png)) as source:
        if source.width * source.height > 16000000 or min(source.size) < 38:
            raise NavigationBlocked('Word capture size unsupported')
        image = np.asarray(source.convert('RGB'))
    matches = []
    for outcome, data in templates.items():
        with Image.open(io.BytesIO(data)) as source:
            if source.size != (38, 38):
                continue
            rgba = np.asarray(source.convert('RGBA'))
        mask = (rgba[:, :, 3] >= 250).astype(np.uint8)
        if int(mask.sum()) < 40:
            continue
        response = cv2.matchTemplate(image, rgba[:, :, :3], cv2.TM_SQDIFF_NORMED, mask=mask)
        response = np.nan_to_num(response, nan=1., posinf=1., neginf=1.)
        for _ in range(2):
            distance, _, (x, y), _ = cv2.minMaxLoc(response)
            if distance >= .001:
                break
            matches.append(dict(app_outcome=outcome, distance=distance, roi=[x, y, x+38, y+38]))
            response[max(0,y-19):y+20, max(0,x-19):x+20] = 1.
    match = matches[0] if len(matches) == 1 and matches[0]['app_outcome'] in OUTCOMES else {}
    return dict(app_outcome=match.get('app_outcome'), roi=match.get('roi'), matches=matches,
                source='installed_apk_word_icon_pixels', screen_sha256=hashlib.sha256(png).hexdigest(),
                screen_kind='target_checkpointed_png', xml_used=False,
                reason=None if match else 'word_png_icon_missing_or_ambiguous')


def word_frame(raw):
    view = Screen(raw)
    if any(view.find(rid) for rid in (
            "layoutMessageBox", "flCoverPageOverlay", "flStagePopupOverlay",
            "flTodayLessonCoverOverlay", "layoutSingleQuiz", "tvConvGuide")):
        raise NavigationBlocked("Word screen is covered or overlaps another learning state")
    card = view.one("cvStudyCard")
    view.one("layoutWord", scope=card)
    view.one("txtPinyin", scope=card)
    controls = view.one("rlBottomControlBar")
    view.one("ivCtrlNext", scope=controls)
    return view


def observe_word(raw, png, templates):
    """Match only the scoped feedback ROI; all other colors/shapes are unknown."""
    import numpy as np
    from PIL import Image

    view = word_frame(raw)
    card = view.one("cvStudyCard")
    speak = view.one("llSpeak", scope=card)
    icon = view.one("ivStudyWordSpeakIcon", scope=speak)
    if view.one("ivStudyWordSpeakIcon") is not icon:
        raise NavigationBlocked("Ambiguous word feedback")
    box = _bounds(icon)
    with Image.open(io.BytesIO(png)) as source:
        if source.width * source.height > 16000000 or box[2] > source.width or box[3] > source.height:
            raise NavigationBlocked("Word feedback outside capture")
        pixels = np.asarray(source.convert("RGB").crop(box), dtype=np.float32)
    distances = {}
    for outcome, data in templates.items():
        with Image.open(io.BytesIO(data)) as source:
            # This build ships 38px nodpi icons. Unknown scaling fails closed.
            if source.size != (box[2] - box[0], box[3] - box[1]):
                continue
            rgba = np.asarray(source.convert("RGBA"), dtype=np.float32)
        mask = rgba[:, :, 3] >= 250
        if int(mask.sum()) < 40:
            continue
        distances[outcome] = float(np.abs(pixels[mask] - rgba[:, :, :3][mask]).mean() / 255)
    ranked = sorted(distances, key=distances.get)
    matched = (len(ranked) == 4 and distances[ranked[0]] < .025
               and distances[ranked[1]] - distances[ranked[0]] > .08)
    outcome = ranked[0] if matched and ranked[0] in OUTCOMES else None
    return dict(app_outcome=outcome, source="installed_apk_word_icon_pixels", roi=list(box),
                distances=distances, screen_sha256=view.sha256,
                reason=None if outcome else "word_feedback_pixels_inconclusive")
