"""Local vector finish: directional short hair, layered folds and expression poses.

No raster source or external service is used. Fixed seeds keep the coat stable
between animation frames; texture moves with each body part, never with the screen.
"""
import copy
import math
import random
import xml.etree.ElementTree as ET

NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)


def node(tag, **attrs):
    return ET.Element(f'{{{NS}}}{tag}', {k.replace('_', '-'): str(v) for k, v in attrs.items()})


def finish(svg, pose='sit', traits=None):
    from avatar_traits import load, recolor
    traits = traits or load()
    folds = f"{traits['proportions']['fold_depth']:.2f}".lstrip('0')
    root = ET.fromstring(svg)
    defs = root.find(f'{{{NS}}}defs')
    if pose.startswith('point-'):
        # Interim pointing pose (F5 replaces it): a raised front paw toward the right, under the jowls.
        paw = ET.fromstring(f'''<g xmlns="{NS}">
<path d="M196 206C228 192 258 180 282 171c16-5 26 12 12 21-24 15-50 32-74 50-10-8-18-20-24-36z" fill="url(#coat)" stroke="#985a32" stroke-width="1.5"/>
<path d="M212 226q32-18 66-37m-58 49q30-20 60-38" fill="none" stroke="#985a32" stroke-width="4" opacity=".45" stroke-linecap="round"/>
<ellipse cx="294" cy="182" rx="19" ry="15" fill="url(#coat)" stroke="#985a32" stroke-width="1.5"/>
<path d="M298 172v10m8-7v10m-15-9v9" stroke="#895b38" stroke-width="1.8" stroke-linecap="round"/></g>''')
        head = next(e for e in root if e.tag.endswith('g') and e.get('transform', '').startswith('translate(-'))
        root.insert(list(root).index(head), paw)
    rng = random.Random(731)
    for name, muzzle in [('hair', False), ('velvet', True)]:
        pattern = node('pattern', id=name, width=80, height=80, patternUnits='userSpaceOnUse')
        # Short, uneven guard hairs, with a finer contrasting undercoat. No fur tile images.
        for i in range(950 if not muzzle else 850):
            x, y = rng.uniform(0, 80), rng.uniform(0, 80)
            length = rng.uniform(1.5, 3.8) if not muzzle else rng.uniform(.4, 1.5)
            dx = math.sin(x / 17 + y / 29) * .6
            light = i % 3 != 0
            pattern.append(node('path', d=f'M{x:.2f} {y:.2f}q{dx:.2f} {length*.5:.2f} {dx+.5:.2f} {length:.2f}',
                                fill='none', stroke=('#ffe1a6' if light else '#593c26') if not muzzle else ('#cab28a' if light else '#292c25'),
                                stroke_width=rng.uniform(.25, .55), stroke_linecap='round', opacity='.28' if light else '.20'))
        defs.append(pattern)
    pores = node('pattern', id='pores', width=12, height=12, patternUnits='userSpaceOnUse')
    for _ in range(22):
        x, y = rng.uniform(0, 12), rng.uniform(0, 12)
        pores.append(node('circle', cx=x, cy=y, r=rng.uniform(.25, .65), fill='#c8bea2', opacity='.24'))
    defs.append(pores)

    for parent in list(root.iter()):
        for element in list(parent):
            fill = element.get('fill', '')
            if fill in ('url(#coat)', 'url(#chest)', 'url(#fur)', 'url(#muzzle)', 'url(#ear)', 'url(#nose)'):
                overlay = copy.deepcopy(element)
                overlay.set('fill', 'url(#pores)' if fill == 'url(#nose)' else 'url(#velvet)' if fill == 'url(#muzzle)' else 'url(#hair)')
                overlay.set('stroke', 'none')
                parent.insert(list(parent).index(element) + 1, overlay)
            # Folds have a soft shaded underside and a rounded lit crest, not flat ink outlines.
            if element.tag.endswith('path') and element.get('fill') == 'none' and element.get('stroke', '').startswith('#'):
                width = float(element.get('stroke-width', '0'))
                if 3 <= width <= 7 and element.get('stroke') not in ('#f0c58d', '#fff0ce', '#9a9580', '#d2c6b2'):
                    for extra, opacity in [(7, '.055'), (3, '.10')]:
                        shadow = copy.deepcopy(element)
                        shadow.set('stroke', '#482a19')
                        shadow.set('stroke-width', str(width + extra))
                        shadow.set('opacity', opacity)
                        parent.insert(list(parent).index(element), shadow)
                    element.set('stroke-width', str(width * .7))
                    element.set('opacity', folds)

    # Expressive head tilt; feet remain planted and the hitbox remains unchanged.
    head = next((e for e in root if e.tag.endswith('g') and e.get('transform', '').startswith('translate(-')), None)
    if head is not None:
        # watching: attentive head while jobs are watched (interim pose; F5 replaces it).
        angle = {'curious-left': -9, 'curious-right': 9, 'watching': -4, 'point-left': 7, 'point-right': 7,
                 'celebrate': -3}.get(pose, 0)
        if pose.startswith('pet-'):
            angle = 3.5 * math.sin(int(pose.split('-')[1]) * math.tau / 8)
        head.set('transform', f'rotate({angle:.2f} 160 210) ' + head.get('transform'))
        if pose in ('curious-left', 'curious-right', 'watching', 'point-left', 'point-right'):
            # Nose lifts slightly toward the cursor; eyes remain small and deep set.
            head.set('transform', head.get('transform') + ' translate(0 -3)')
        elif pose == 'celebrate':
            head.set('transform', head.get('transform') + ' translate(0 -7)')  # Chin up, proud.
        elif pose == 'reading':
            head.set('transform', head.get('transform') + ' translate(0 5)')  # Looking down at the book.
    if pose.startswith('pet-'):
        frame = int(pose.split('-')[1])
        tail = node('g', transform=f'rotate({12 * math.sin(frame * math.tau / 8):.2f} 233 235)')
        paths = [e for e in root if e.tag.endswith('path')][:2]
        index = list(root).index(paths[0])
        for element in paths:
            root.remove(element)
            tail.append(element)
        root.insert(index, tail)
    if pose == 'celebrate':
        # Interim celebration (F5 replaces it): confetti around the head, drawn on top.
        confetti = ET.fromstring(f'''<g xmlns="{NS}" opacity=".95">
<rect x="46" y="44" width="10" height="5" rx="1.5" fill="#fcaf3e" transform="rotate(-25 51 46)"/>
<rect x="262" y="38" width="10" height="5" rx="1.5" fill="#8ae234" transform="rotate(30 267 40)"/>
<rect x="30" y="118" width="9" height="5" rx="1.5" fill="#729fcf" transform="rotate(15 34 120)"/>
<rect x="282" y="112" width="9" height="5" rx="1.5" fill="#ef2929" transform="rotate(-20 286 114)"/>
<circle cx="88" cy="22" r="4" fill="#ad7fa8"/><circle cx="232" cy="18" r="4" fill="#fcaf3e"/>
<circle cx="160" cy="10" r="3.5" fill="#8ae234"/><circle cx="24" cy="78" r="3" fill="#ef2929"/>
<circle cx="296" cy="74" r="3" fill="#729fcf"/></g>''')
        root.append(confetti)
    elif pose == 'reading':
        # Interim reading pose (F5 replaces it): an open book held at the chest.
        book = ET.fromstring(f'''<g xmlns="{NS}">
<path d="M98 216l60 10v42l-60-10z" fill="#3465a4"/><path d="M222 216l-60 10v42l60-10z" fill="#3465a4"/>
<path d="M102 212l56 10v40l-56-9z" fill="#fbf7ee" stroke="#d3cab7"/><path d="M218 212l-56 10v40l56-9z" fill="#fbf7ee" stroke="#d3cab7"/>
<path d="M110 222l40 6m-40 4l40 6m-40 4l40 6m-40 4l30 5M210 222l-40 6m40 4l-40 6m40 4l-40 6m40 4l-30 5"
 stroke="#9a9580" stroke-width="1.6" stroke-linecap="round"/>
<path d="M160 224v40" stroke="#204a87" stroke-width="2"/></g>''')
        root.append(book)
    if pose == 'point-left':
        # Mirror image of point-right: the paw points to the left.
        mirror = node('g', transform='matrix(-1 0 0 1 320 0)')
        for element in [e for e in root if not e.tag.endswith('defs')]:
            root.remove(element)
            mirror.append(element)
        root.append(mirror)
    # Warmer copper coat, cream highlights and deeper brown, or the measured palette.
    return recolor(ET.tostring(root, encoding='unicode'), traits)
