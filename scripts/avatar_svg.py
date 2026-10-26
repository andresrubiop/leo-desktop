"""Avatar vectorial local de Leo: silueta transparente, pliegues y volumen.

Interpretación ilustrada de los rasgos observados en IMG_1496.JPG; no reconstrucción 3D.
"""


def _base_sprite(pose, provisional=False, proportions=None):
    proportions = proportions or {}
    head_width = proportions.get('head_width', 1.0)
    ear_size = proportions.get('ear_size', 1.0)
    sleeping = pose == "sleep"
    blinking = pose in {"sleep", "blink"}
    happy = pose == "happy"
    gold = "#bcb7ac" if provisional else "#d59a58"
    light = "#e3ded3" if provisional else "#f0c58d"
    deep = "#74716b" if provisional else "#985a32"
    eyes = ('<path d="M102 116q12 10 25 0m54 0q12 10 25 0" fill="none" stroke="#443528" stroke-width="5" stroke-linecap="round"/>'
            if blinking else '''<path d="M100 116q15-13 31 1-17 13-31-1m78 1q16-14 31-1-14 13-31 1" fill="#382c24"/>
<ellipse cx="119" cy="116" rx="5" ry="6" fill="#6b4e30"/><ellipse cx="191" cy="116" rx="5" ry="6" fill="#6b4e30"/>
<circle cx="120" cy="115" r="3.5" fill="#211e1a"/><circle cx="190" cy="115" r="3.5" fill="#211e1a"/>
<circle cx="121" cy="113" r="1.5" fill="#fff0ce"/><circle cx="191" cy="113" r="1.5" fill="#fff0ce"/>''')
    feet = '''<path d="M99 225q-8 27-8 51l-9 10q-12 13 9 13h37q13-1 8-13l-3-15 6-44" fill="url(#coat)"/>
<path d="M179 228l5 45-4 13q-5 13 10 13h37q22-4 7-15l-11-10q-1-28-7-46" fill="url(#coat)"/>
<path d="M98 289v8m12-9v10m91-10v10m12-9v9" stroke="#865533" opacity=".7" stroke-width="2.5" stroke-linecap="round"/>
<path d="M96 250q18 10 37 1m51 1q16 8 37-1" fill="none" stroke="#995f36" stroke-width="4" opacity=".6"/>
<path d="M96 246q18 8 37 1m52 1q16 6 34-1" fill="none" stroke="#f0c58d" stroke-width="2.5" opacity=".8"/>'''
    body = ('''<ellipse cx="164" cy="257" rx="122" ry="43" fill="url(#coat)"/>
<path d="M79 272q-39 11-31 25h90q18-8-4-23m58 1q45 9 68 19" fill="url(#coat)" stroke="#995e35" stroke-width="2"/>
<path d="M198 235q31 14 29 41m-14-42q31 17 28 43m-5-35q22 14 20 32" fill="none" stroke="#995e35" stroke-width="4" opacity=".6"/>
<path d="M201 232q30 15 28 40m-12-40q30 17 27 41" fill="none" stroke="#f0c58d" stroke-width="2.5"/>'''
            if sleeping else f'''<path d="M94 162C59 182 41 217 43 259c1 28 23 40 52 31q61 13 127 0c29 9 51-5 51-33-1-43-20-76-53-96z" fill="url(#coat)" stroke="{deep}" stroke-width="1.5"/>
<ellipse cx="158" cy="234" rx="77" ry="58" fill="url(#chest)"/>
<path d="M72 214q-18 26-9 51m183-49q15 25 7 50M94 231q64 31 130-2m-123 21q54 27 115-1m-108 21q47 19 100-2" fill="none" stroke="#9b6037" opacity=".5" stroke-width="5" stroke-linecap="round"/>
<path d="M95 226q64 29 127-2m-119 21q53 24 110-1m-104 21q47 17 96-2" fill="none" stroke="{light}" opacity=".8" stroke-width="3" stroke-linecap="round"/>
{feet}
<path d="M92 205q10 31 37 34 31 13 58-1 29-6 37-34" fill="url(#coat)"/>
<path d="M98 211q12 18 35 22 29 12 55-1 22-6 30-24m-107 23q45 27 94-4" fill="none" stroke="{deep}" stroke-width="4" opacity=".65" stroke-linecap="round"/>
<path d="M101 207q13 17 34 21 28 10 52-1 20-6 28-22" fill="none" stroke="{light}" stroke-width="3" opacity=".8"/>''')
    # A broader, lower head and smaller ears make the silhouette stocky rather
    # than simply scaling the whole avatar (feet and desktop hitbox stay fixed).
    head_transform = (f"translate(-10 110) scale({1.04 * head_width:.4g} .79)" if sleeping
                      else f"translate(-14 34) scale({1.09 * head_width:.4g} .88)")
    # Small folded ears scale around their base so they stay attached to the skull.
    ears = [('<path d="M105 48C91 36 77 42 80 56l11 23q12-4 19-19z" fill="url(#ear)" stroke="{deep}" stroke-width="3"/>\n'
             '<path d="M101 50q-17-8-14 4l7 13 8-11" fill="{deep}" opacity=".7"/>', 100, 66),
            ('<path d="M205 48q20-15 29-2 6 9-9 31-13-3-20-18z" fill="url(#ear)" stroke="{deep}" stroke-width="3"/>\n'
             '<path d="M211 51q17-10 15 2l-8 13-8-11" fill="{deep}" opacity=".7"/>', 212, 66)]
    ears = '\n'.join(e.format(deep=deep) if ear_size == 1 else
                     f'<g transform="translate({x} {y}) scale({ear_size:.3g}) translate({-x} {-y})">{e.format(deep=deep)}</g>'
                     for e, x, y in ears)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320">
<defs>
<radialGradient id="coat" cx="35%" cy="20%" r="85%"><stop stop-color="{light}"/><stop offset=".45" stop-color="{gold}"/><stop offset="1" stop-color="{deep}"/></radialGradient>
<radialGradient id="chest" cx="44%" cy="18%" r="90%"><stop stop-color="{light}"/><stop offset="1" stop-color="{gold}"/></radialGradient>
<linearGradient id="ear" x2=".7" y2="1"><stop stop-color="{deep}"/><stop offset=".5" stop-color="#70452d"/><stop offset="1" stop-color="{gold}"/></linearGradient>
<radialGradient id="muzzle" cx="42%" cy="20%" r="85%"><stop stop-color="#a48b67"/><stop offset=".45" stop-color="#796449"/><stop offset="1" stop-color="#493f31"/></radialGradient>
<linearGradient id="nose" x2=".4" y2="1"><stop stop-color="#5f594c"/><stop offset=".45" stop-color="#34342c"/><stop offset="1" stop-color="#242821"/></linearGradient>
</defs>
<ellipse cx="163" cy="303" rx="119" ry="10" fill="#21180e" opacity=".13"/>
<path d="M227 240c42-7 64-30 45-50-20-19-44 10-25 19 10 4 18-8 10-11" fill="none" stroke="{deep}" stroke-width="20" stroke-linecap="round"/>
<path d="M227 235c40-8 55-28 40-43-11-10-25-3-25 7" fill="none" stroke="{gold}" stroke-width="12" stroke-linecap="round"/>
{body}
<g transform="{head_transform}">
<path d="M83 83c-4-34 28-57 68-58 44-2 80 21 81 55l11 56c4 29-15 62-43 74-31 14-66 11-95-6-26-16-40-45-32-72z" fill="url(#coat)" stroke="{deep}" stroke-width="2"/>
{ears}
<g fill="none" stroke-linecap="round">
<path d="M106 57c8-17 22-19 36-10q11 8 20-1c14-9 29-2 36 10M98 76q13-22 36-13l17 11q8 5 17-7 21-16 43 9M96 94q18-19 43-9m30 0q21-11 44 9" stroke="#9d6339" stroke-width="6" opacity=".8"/>
<path d="M109 51q17-17 34-6t22-1q16-7 29 6M104 69q15-16 30-8l17 10q9 2 18-9 17-10 34 5M102 89q20-13 38-8m28 0q17-6 37 9" stroke="{light}" stroke-width="3" opacity=".85"/>
<path d="M145 50q-11 22 2 43m18-43q12 22-1 44" stroke="#ab6b3a" stroke-width="3" opacity=".65"/>
<path d="M86 104q-17 26-1 50m-5-32q-17 29 4 53m145-71q18 28 0 51m7-29q14 27-7 50" stroke="#98633d" stroke-width="4" opacity=".6"/>
<path d="M94 105q20-13 39 1m43 0q19-13 40-1" stroke="#aa7144" stroke-width="7"/>
</g>
{eyes}
<path d="M103 172q-4 48 49 51 51 0 56-49" fill="url(#muzzle)"/>
<path d="M116 125c-28-8-44 5-44 29-2 31 17 50 42 51 21 3 25-6 40-5 19 0 25 9 47 2 27-7 40-28 35-52-4-25-23-33-48-24-25-17-48-15-72-1" fill="url(#muzzle)"/>
<path d="M116 147q-23 12-15 30m97-30q24 12 12 32" fill="none" stroke="#b49b74" stroke-width="3" opacity=".45" stroke-linecap="round"/>
<path d="M124 129q29-18 57 0c4 9-6 25-15 29-9 3-15 3-23 0-11-5-22-18-19-29" fill="url(#nose)"/>
<path d="M133 129q20-9 39 0" fill="none" stroke="#9a9580" stroke-width="3" opacity=".6" stroke-linecap="round"/>
<ellipse cx="137" cy="141" rx="6" ry="4" fill="#242821"/><ellipse cx="171" cy="141" rx="6" ry="4" fill="#242821"/>
<path d="M154 160v20m-35 6q14-3 35 0 20 5 36-3" fill="none" stroke="#373329" stroke-width="3.5" stroke-linecap="round"/>
{'<path d="M143 186q11 7 23 0l-1 13q-11 15-22-1z" fill="#66526c"/><path d="M154 192v9" stroke="#493c52" stroke-width="2"/>' if happy else ''}
<g fill="#4c4534" opacity=".65"><circle cx="104" cy="157" r="1.5"/><circle cx="111" cy="164" r="1.5"/><circle cx="103" cy="173" r="1.2"/><circle cx="199" cy="157" r="1.5"/><circle cx="207" cy="166" r="1.4"/><circle cx="198" cy="174" r="1.2"/></g>
<path d="M92 183q3 19 25 26m101-27q-6 18-27 25" fill="none" stroke="#9b613a" stroke-width="4" stroke-linecap="round"/>
<path d="M123 213q29 11 59 0" fill="none" stroke="{light}" stroke-width="3" opacity=".7"/>
</g>
{'<g fill="#d2c6b2" font-family="sans-serif" font-weight="bold"><text x="244" y="159" font-size="20">z</text><text x="266" y="138" font-size="15">z</text></g>' if sleeping else ''}
{'<g fill="#d9bb7c"><circle cx="263" cy="56" r="4"/><circle cx="278" cy="48" r="3"/><circle cx="291" cy="49" r="2"/></g>' if pose == 'alert' else ''}
</svg>'''


def _base_walking_sprite(frame, provisional=False, proportions=None):
    """24-frame quadruped walk. Planted feet travel backwards during stance;
    the swing lifts the paw, with two articulated joints and four-beat timing.
    All geometry is local vector artwork, independent of the original photos.
    """
    import math
    proportions = proportions or {}
    head_width = proportions.get('head_width', 1.0)
    ear_size = proportions.get('ear_size', 1.0)
    # A wider head grows back over the neck, keeping the nose inside the 320 px canvas.
    ear_scale = '' if ear_size == 1 else f' translate(197 91) scale({ear_size:.3g}) translate(-197 -91)'
    phase = frame / 24
    bob = 2.2 * math.cos(phase * math.tau * 2)
    gold = '#bcb7ac' if provisional else '#d59a58'
    light = '#e3ded3' if provisional else '#f0c58d'
    deep = '#74716b' if provisional else '#985a32'

    def leg(hip_x, offset, far=False):
        t = (phase + offset) % 1
        if t < .62:
            foot_x = hip_x + 19 - 38 * t / .62
            lift = 0
        else:
            u = (t - .62) / .38
            foot_x = hip_x - 19 + 38 * (.5 - .5 * math.cos(math.pi * u))
            lift = 18 * math.sin(math.pi * u)
        hip_y = 237 + bob
        foot_y = (287 if far else 298) - lift
        dx, dy = foot_x - hip_x, foot_y - hip_y
        d = math.hypot(dx, dy)
        # Equal-length IK, bending rear knees forward and front elbows back.
        bend = math.sqrt(max(0, 35 ** 2 - (d / 2) ** 2))
        sign = 1 if hip_x < 150 else -1
        knee_x = (hip_x + foot_x) / 2 + sign * dy / d * bend
        knee_y = (hip_y + foot_y) / 2 - sign * dx / d * bend
        shade = deep if far else 'url(#fur)'
        return f'''<g><path d="M{hip_x} {hip_y:.2f} Q{hip_x-3} {hip_y+12:.2f} {knee_x:.2f} {knee_y:.2f} L{foot_x:.2f} {foot_y-7:.2f}"
 fill="none" stroke="{shade}" stroke-width="27" stroke-linejoin="round" stroke-linecap="round"/>
 <ellipse cx="{foot_x+5:.2f}" cy="{foot_y-4:.2f}" rx="19" ry="8" fill="{shade}"/>
 <path d="M{foot_x+9:.2f} {foot_y-6:.2f}v5m5-5v4" stroke="#895b38" stroke-width="1.6" stroke-linecap="round"/></g>'''

    tail = 6 * math.sin(phase * math.tau)
    head = 1.8 * math.sin(phase * math.tau * 2 + .6)
    ear = 3 * math.sin(phase * math.tau * 2 + 1.1)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320">
<defs>
<radialGradient id="fur" cx="40%" cy="18%" r="90%"><stop stop-color="{light}"/><stop offset=".55" stop-color="{gold}"/><stop offset="1" stop-color="{deep}"/></radialGradient>
<linearGradient id="muzzle" x2=".4" y2="1"><stop stop-color="#a38a66"/><stop offset=".6" stop-color="#776247"/><stop offset="1" stop-color="#443b30"/></linearGradient>
<linearGradient id="nose" x2="0" y2="1"><stop stop-color="#59574b"/><stop offset="1" stop-color="#242821"/></linearGradient>
</defs>
<ellipse cx="161" cy="305" rx="128" ry="7" fill="#21180e" opacity=".12"/>
{leg(99, .5, True)}{leg(204, .75, True)}
<g transform="translate(0 {bob:.2f})">
<g transform="rotate({tail:.2f} 71 185)"><path d="M73 189C30 187 23 142 43 135c25-10 41 19 23 28-17 8-22-14-11-15" fill="none" stroke="{deep}" stroke-width="16" stroke-linecap="round"/><path d="M73 185C33 178 29 143 46 141q18-6 20 13" fill="none" stroke="{gold}" stroke-width="10" stroke-linecap="round"/></g>
<path d="M48 191C45 163 75 143 118 148L201 148q44 3 49 47l-5 35q-4 24-34 32-30 13-67 4l-55-3q-49-9-41-72" fill="url(#fur)" stroke="{deep}" stroke-width="1.5"/>
<path d="M78 162q-22 24-17 55m36-59q-23 27-15 59m33-59q-16 22-12 43m21-48q-8 15-6 31M123 232q43 22 90 3m-88 11q41 20 77 3m-33-85q32 29 66 17m-70 0q33 27 67 14" fill="none" stroke="{deep}" stroke-width="5" opacity=".6" stroke-linecap="round"/>
<path d="M79 157q-20 24-17 53m36-56q-21 25-15 52m34-54q-17 22-12 43M126 228q40 20 86 2m-83 11q39 17 72 3" fill="none" stroke="{light}" stroke-width="3" opacity=".8"/>
</g>
{leg(105, 0)}{leg(210, .25)}
<g transform="translate(0 {bob:.2f}) rotate({head:.2f} 210 177) translate({-16 + 308 * 1.045 * (1 - head_width):.4g} 22) scale({1.045 * head_width:.4g} .9)">
<path d="M181 176q-20-29-13-61 3-41 42-47 44-10 66 25l12 40q14 34-8 61-17 24-47 23-32 7-52-41z" fill="url(#fur)" stroke="{deep}" stroke-width="1.5"/>
<path d="M202 85q14-12 27-7 14-2 24 11m-54 9q15-12 30-6 18-2 32 10m-51 9q26-9 53 5M176 132q-9 29 15 48m-16-22q-2 28 24 39m-16-11q12 25 40 25m-24-4q25 19 52 3" fill="none" stroke="{deep}" stroke-width="4" opacity=".65" stroke-linecap="round"/>
<path d="M205 80q24-10 44 3m-43 11q28-10 49 4m-40 10q26-7 43 3" fill="none" stroke="{light}" stroke-width="2.5" stroke-linecap="round"/>
<g transform="rotate({ear:.2f} 197 91){ear_scale}"><path d="M207 88q-20-15-28-3-2 8 12 21 12-6 16-18" fill="{deep}"/><path d="M201 89q-15-10-14-1l6 10" fill="#805234"/></g>
<path d="M238 121q14-10 24 1-10 11-24-1" fill="#352a22"/><circle cx="251" cy="122" r="4" fill="#211e1a"/><circle cx="252" cy="120" r="1.5" fill="#fff0ce"/>
<path d="M239 117q14-10 26 1" fill="none" stroke="{deep}" stroke-width="5" stroke-linecap="round"/>
<path d="M250 134q22-8 43 1 15 4 15 19l-1 21q-3 20-29 22-34 3-47-17-11-27 19-46" fill="url(#muzzle)"/>
<path d="M281 137q14-7 24 2 4 9-6 16-14 2-18-18" fill="url(#nose)"/>
<path d="M285 139q8-3 14 0" stroke="#999381" stroke-width="2.5" fill="none" stroke-linecap="round"/>
<path d="M242 178q26 13 52-5" fill="none" stroke="#393329" stroke-width="3" stroke-linecap="round"/>
<path d="M245 149q-10 10-3 17" fill="none" stroke="#baa07a" stroke-width="2.5" opacity=".6"/>
<g fill="#443e31" opacity=".65"><circle cx="269" cy="159" r="1.5"/><circle cx="276" cy="165" r="1.5"/><circle cx="264" cy="167" r="1.3"/></g>
<path d="M202 210q22 12 48 1" fill="none" stroke="{light}" stroke-width="3"/>
</g></svg>'''


def sprite(pose, provisional=False, traits=None):
    from avatar_detail import finish
    from avatar_traits import load
    traits = traits or load()
    base = 'happy' if pose.startswith('pet-') or pose == 'celebrate' else 'sit' if pose.startswith('curious-') else pose
    if pose.startswith('pet-') and int(pose.split('-')[1]) in (2, 3, 4):
        base = 'blink'
    return finish(_base_sprite(base, provisional, traits['proportions']), pose, traits)


def walking_sprite(frame, provisional=False, traits=None):
    from avatar_detail import finish
    from avatar_traits import load
    traits = traits or load()
    return finish(_base_walking_sprite(frame, provisional, traits['proportions']), 'walk', traits)
