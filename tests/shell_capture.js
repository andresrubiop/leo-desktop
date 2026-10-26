// Solo bajo scripts/test_shell.py --vision-only: compositor aislado, escena ficticia y modelos locales.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};

async function probe(argument) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE});
    const process = launcher.spawnv(['/usr/bin/python3', GLib.getenv('LEO_PROJECT_ROOT') + '/tests/capture_probe.py', argument]);
    const [stdout, stderr] = await new Promise((resolve, reject) => {
        process.communicate_utf8_async(null, null, (proc, result) => {
            try {
                const [, output, errors] = proc.communicate_utf8_finish(result);
                resolve([output, errors]);
            } catch (error) { reject(error); }
        });
    });
    if (!process.get_successful()) throw new Error('Capture failed: ' + stdout + '\n' + stderr);
    print(stdout);
}

function callerDenied(method, parameters) {
    return new Promise(resolve => {
        Gio.DBus.session.call('org.gnome.Shell', '/io/github/leo/Capture', 'io.github.leo.Capture', method,
            parameters, null, Gio.DBusCallFlags.NONE, 5000, null, (bus, result) => {
                try { bus.call_finish(result); resolve(false); } catch (_) { resolve(true); }
            });
    });
}

async function waitFor(check, what, seconds = 20) {
    for (let i = 0; i < seconds * 10; i++) {
        if (check()) return;
        await Scripting.sleep(100);
    }
    throw new Error('Timed out waiting for ' + what);
}

function daemon(instance, payload) {
    return new Promise(resolve => instance._call(payload, resolve, {timeout: 20000}));
}

// F6.3: shortcut, 👁 and region capture exactly once; Esc captures nothing; follow-ups reuse the capture;
// closing the balloon forgets it.
async function lookGestures(instance) {
    const service = instance._captureService;
    await waitFor(() => instance._shortcutAction, 'the look shortcut grab');
    print(`LEO_LOOK_SHORTCUT_GRAB_PASS: atajo ${instance._shortcut} registrado en GNOME`);
    let before = service.captures;
    instance._lookNow('window');  // The shortcut handler.
    await waitFor(() => instance._attachment && !instance._looking, 'the shortcut capture');
    // Window scope; with no window left open (the GTK test window is gone), the monitor, as designed.
    if (service.captures !== before + 1 || !['window', 'monitor'].includes(instance._attachment.scope) || !instance._bubble.isOpen)
        throw new Error('The shortcut must capture once and open the balloon: ' + JSON.stringify(instance._attachment));
    before = service.captures;
    instance._lookButton.emit('clicked', 1);
    await waitFor(() => service.captures === before + 1 && !instance._looking, 'the 👁 capture');
    print('LEO_LOOK_GESTURES_PASS: atajo y 👁 capturan una sola vez cada uno y abren el globo con la captura recordada');
    instance._bubble.close(0);
    await Scripting.sleep(500);
    const status = await daemon(instance, {op: 'look', action: 'status', args: {}});
    if (!status.ok || status.attached !== null || instance._attachment)
        throw new Error('Closing the balloon must forget the capture: ' + JSON.stringify(status));
    print('LEO_LOOK_FORGET_PASS: al cerrar el globo, el daemon ya no recuerda la captura');
    const seat = global.stage.get_context().get_backend().get_default_seat();
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    before = service.captures;
    const cancelled = instance._selectRegion();
    await Scripting.sleep(600);
    keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
    await cancelled;
    await Scripting.sleep(800);
    if (service.captures !== before || instance._attachment)
        throw new Error('A cancelled region must not capture');
    print('LEO_LOOK_REGION_CANCEL_PASS: Esc en la selección de zona no captura nada');
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const selected = instance._selectRegion();
    await Scripting.sleep(600);
    pointer.notify_absolute_motion(GLib.get_monotonic_time(), 100, 140);
    await Scripting.sleep(100);
    pointer.notify_button(GLib.get_monotonic_time(), 1, Clutter.ButtonState.PRESSED);
    for (let i = 1; i <= 10; i++) {
        pointer.notify_absolute_motion(GLib.get_monotonic_time(), 100 + 90 * i, 140 + 38 * i);
        await Scripting.sleep(40);
    }
    pointer.notify_button(GLib.get_monotonic_time(), 1, Clutter.ButtonState.RELEASED);
    await selected;
    await waitFor(() => instance._attachment?.scope === 'region' && !instance._looking, 'the region capture');
    if (service.captures !== before + 1)
        throw new Error('A selected region must capture exactly once');
    print('LEO_LOOK_REGION_PASS: zona arrastrada con el selector de GNOME → una captura recordada');
    before = service.captures;
    instance._entry.set_text('¿Qué código de cuatro cifras aparece? Responde solo con las cifras.');
    instance._send();
    await waitFor(() => !instance._busy, 'the follow-up answer', 180);
    if (!instance._reply.text.includes('7391') || service.captures !== before)
        throw new Error('The follow-up must answer from the remembered capture: ' + instance._reply.text);
    print('LEO_LOOK_FOLLOWUP_PASS: pregunta de seguimiento respondida con la captura en memoria, sin recapturar');
    instance._bubble.close(0);
    await Scripting.sleep(300);
}

// Agent mascots from session files in the private HOME; Leo living in a window; Leo atento by posture.
async function petsAndWatch(instance) {
    const home = GLib.get_home_dir();
    const today = GLib.DateTime.new_now_local().format('%Y/%m/%d');
    const files = [`${home}/.claude/projects/-tmp-demo/a.jsonl`, `${home}/.claude/projects/-tmp-demo/b.jsonl`,
        `${home}/.codex/sessions/${today}/rollout-1.jsonl`];
    for (const path of files) {
        GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);
        GLib.file_set_contents(path, '{}');
    }
    await waitFor(() => instance._agentPets.size === 3, 'three agent mascots', 30);
    await Scripting.sleep(400);
    const pets = [...instance._agentPets.values()];
    const tools = pets.map(item => item.tool).sort().join(',');
    const near = pets.every(item => item.actor.visible && Math.abs(item.actor.x - instance._pet.x) < 260 &&
        Math.abs(item.actor.y - instance._pet.y) < 200 && item.actor.icon_size <= Math.round(instance._pet.width / 8));
    if (tools !== 'claude,claude,codex' || !near)
        throw new Error('Mascots must follow Leo, one per session, a tenth of his size: ' + tools);
    print(`LEO_PETS_PASS: 2 sesiones de Claude y 1 de Codex → ${pets.length} mascotitas de ${pets[0].actor.icon_size} px junto a Leo`);
    for (const path of files) Gio.File.new_for_path(path).delete(null);
    await waitFor(() => instance._agentPets.size === 0, 'mascots leaving', 30);
    print('LEO_PETS_GONE_PASS: al terminar las sesiones, las mascotitas se van');

    // Leo dropped inside the window: slow walk within its borders, following it; errands do not pull him out.
    // A real GTK window (the perf helper's test windows do not outlive the long vision checks).
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
    launcher.setenv('WAYLAND_DISPLAY', 'gnome-shell-test-display', true);
    launcher.setenv('GDK_BACKEND', 'wayland', true);
    // F6.4: an editor-like title names a git project in the private HOME.
    const project = `${home}/Proyecto`;
    GLib.mkdir_with_parents(`${project}/docs`, 0o700);
    GLib.file_set_contents(`${project}/docs/progress.md`, '## Fase\nPróximo paso: probar a Leo en GNOME.\n');
    GLib.spawn_sync(null, ['/usr/bin/git', 'init', '-q', '-b', 'main', project], null, GLib.SpawnFlags.DEFAULT, null);
    // F7.1: the document named by the title, inside an authorized folder, feeds the diary and the answers.
    GLib.file_set_contents(`${project}/notas.md`, '# Notas del experimento\n\nEl learning_rate del experimento principal ' +
        'es 0.003 (definido en config.yaml).\n');
    const authorized = await daemon(instance, {op: 'settings', changes: {files: true,
        folders: [{id: 'proyecto', path: project, label: 'Proyecto', write: false}]}});
    if (!authorized.ok) throw new Error('Authorizing the project folder failed: ' + JSON.stringify(authorized));
    const app = launcher.spawnv(['/usr/bin/python3', GLib.getenv('LEO_PROJECT_ROOT') + '/tests/window_app.py',
        'notas.md — Proyecto — Visual Studio Code', "Traceback (most recent call last):\nKeyError: 'learning_rate'"]);
    let window = null;
    await waitFor(() => (window = global.get_window_actors().map(actor => actor.meta_window).find(w =>
        w.get_gtk_application_id() === 'io.github.leo.TestWindow' && w.get_frame_rect().height > 300)), 'the GTK test window');
    window.move_frame(true, 200, 200);
    await Scripting.sleep(500);
    let rect = window.get_frame_rect();
    instance._pet.set_position(rect.x + 120, rect.y + 90);
    instance._tryPerch();
    instance._pin();
    if (!instance._perch?.inside) {
        const windows = global.get_window_actors().map(actor => actor.meta_window).map(w => {
            const r = w.get_frame_rect();
            return {title: w.get_title(), rect: [r.x, r.y, r.width, r.height], min: w.minimized, full: w.is_fullscreen(),
                ws: w.located_on_workspace(global.workspace_manager.get_active_workspace()), type: w.get_window_type()};
        });
        throw new Error('Leo dropped inside a window must live in it: ' + JSON.stringify({pet: [instance._pet.x, instance._pet.y,
            instance._pet.width, instance._pet.height], perch: !!instance._perch, windows}));
    }
    const xs = [];
    // Leo rests now and then while strolling: watch up to 20 s, until he has moved a few pixels.
    for (let i = 0; i < 200 && (i < 70 || Math.max(...xs) - Math.min(...xs) < 3); i++) {
        await Scripting.sleep(100);
        xs.push(instance._pet.x);
    }
    rect = window.get_frame_rect();
    const inside = xs.every(x => x >= rect.x - 1 && x <= rect.x + rect.width - instance._pet.width + 1);
    const speed = Math.max(...xs.slice(1).map((x, i) => Math.abs(x - xs[i]))) * 10;  // px per second
    if (!inside || Math.max(...xs) - Math.min(...xs) < 3 || speed > 40)
        throw new Error('Leo must stroll slowly inside the window: ' + JSON.stringify({inside, speed, min: Math.min(...xs), max: Math.max(...xs)}));
    window.move_frame(true, rect.x + 60, rect.y + 40);
    await Scripting.sleep(400);
    rect = window.get_frame_rect();
    instance._walkToProject('demo');
    if (!instance._perch || instance._pet.x < rect.x - 1 || instance._pet.x > rect.x + rect.width - instance._pet.width + 1 ||
        instance._pet.y < rect.y - 1)
        throw new Error('Leo must follow his window and stay in it');
    print(`LEO_PERCH_WALK_PASS: dentro de la ventana camina despacio (máx. ${Math.round(speed)} px/s), sin salir de sus bordes, y la sigue`);

    // Leo atento: inside the window he watches only it.
    instance._set({watch: true, screen: true});
    // The traceback is the window's own content: Leo atento captures that window, not what floats over it.
    await waitFor(() => instance._watchSent === 'perched' && instance._eye.visible, 'watching the window');
    // The third eye opens on Leo's forehead (anchors per pose and walking frame), just above Leo.
    await waitFor(() => instance._eye.scale_y > .9 && instance._eye.visible, 'the third eye opening', 10);
    {
        const frame = instance._lastFrame, anchor = instance._thirdEye?.[frame];
        if (!anchor) throw new Error('No third-eye anchor for ' + frame);
        const k = instance._image.width / 320, mirrored = frame.startsWith('walk') && instance._side < 0;
        const ex = instance._pet.x + instance._pet.translation_x + (mirrored ? 320 - anchor.x : anchor.x) * k;
        const ey = instance._pet.y + instance._pet.translation_y + anchor.y * k;
        const cx = instance._eye.x + instance._eye.width / 2, cy = instance._eye.y + instance._eye.height / 2;
        const siblings = instance._pet.get_parent().get_children();
        if (Math.abs(cx - ex) > 4 || Math.abs(cy - ey) > 4 || siblings.indexOf(instance._eye) < siblings.indexOf(instance._pet))
            throw new Error('The third eye must sit on the forehead, above Leo: ' + JSON.stringify({frame, ex, ey, cx, cy}));
        print(`LEO_THIRD_EYE_PASS: tercer ojo abierto en la frente (${frame}, ${Math.round(cx - instance._pet.x)},${Math.round(cy - instance._pet.y)} px), como sus ojos`);
    }
    const before = await daemon(instance, {op: 'watch', action: 'status', args: {}});
    const settings = (await daemon(instance, {op: 'status'})).settings;
    const flashes = instance._captureService.flashes, quiet = instance._captureService.quiet;
    const observed = await daemon(instance, {op: 'watch', action: 'now', args: {}});
    if (instance._captureService.flashes !== flashes || instance._captureService.quiet <= quiet)
        throw new Error('Leo atento must capture quietly: no flash, nothing of Leo hidden');
    if (!observed.ok || observed.observed?.scope !== 'perched')
        throw new Error('Perched watch must capture only that window: ' + JSON.stringify({observed, before,
            watch: settings.watch, screen: settings.screen, sent: instance._watchSent, local: instance._settings.watch}));
    try {
        await waitFor(() => instance._toast.visible && instance._toast.text.includes('KeyError'), 'the error notice', 30);
    } catch (error) {
        const log = await daemon(instance, {op: 'watch', action: 'status', args: {}});
        throw new Error(error.message + ': ' + JSON.stringify({observed, log: log.log, toast: instance._toast.text}));
    }
    print(`LEO_WATCH_PERCHED_PASS: observa solo su ventana y avisa: «${instance._toast.text}»`);
    await waitFor(() => instance._context?.name === 'Proyecto' && instance._badge.visible, 'the project badge', 30);
    instance._open();
    await Scripting.sleep(300);
    if (instance._badge.visible || !instance._contextLabel.visible || !instance._contextLabel.text.includes('rama main') ||
        !instance._contextLabel.text.includes('Próximo paso'))
        throw new Error('Opening the balloon must show the project card: ' + instance._contextLabel.text);
    instance._entry.set_text('Leo, ¿qué ves?');
    instance._send();
    await waitFor(() => !instance._busy, 'the watch answer', 120);
    if (!instance._reply.text.startsWith('Estoy observando tu ventana') || !instance._reply.text.includes('Proyecto') ||
        !/KeyError|learning_rate|error/i.test(instance._reply.text))
        throw new Error('«¿Qué ves?» must describe what Leo watches: ' + instance._reply.text);
    if (instance._captureService.flashes !== flashes)
        throw new Error('Watching must never flash');
    instance._bubble.close(0);
    await Scripting.sleep(300);
    print(`LEO_CONTEXT_PASS: 📁 discreto, tarjeta «${instance._contextLabel.text.replaceAll('\n', ' | ')}» y «${instance._reply.text.slice(0, 120)}»`);
    // F7.1: what Leo saw is in today's diary, listed when Leo is clicked; a chosen entry frames the next question.
    // The title arrived after the first look; the next look (at most a minute later) writes it: ask for it now.
    const next = await daemon(instance, {op: 'watch', action: 'now', args: {}});
    if (!next.ok) throw new Error('A new look must be possible: ' + JSON.stringify(next));
    const diary = await daemon(instance, {op: 'watch', action: 'seen', args: {}});
    const written = diary.entries?.find(item => item.document?.name === 'notas.md');
    if (!written || written.document.path !== `${project}/notas.md` ||
        !(written.lines ?? []).some(line => /learning_rate|KeyError/.test(line)))
        throw new Error('The diary must hold what Leo saw: ' + JSON.stringify(diary));
    instance._open();
    await waitFor(() => instance._seenLoaded && instance._seenBox.visible && instance._seenList.get_n_children() > 0 &&
        instance._seen[0]?.id === diary.entries.at(-1).id, 'the diary in the balloon', 10);
    const row = instance._seenList.get_child_at_index(0);
    row.emit('clicked', 1);
    if (!instance._seenFocus || !instance._entry.hint_text.startsWith('pregunta sobre lo de las'))
        throw new Error('Choosing an entry must frame the next question');
    const listed = instance._seenList.get_child_at_index(0).get_child().get_first_child().text;
    const question = '¿Qué valor tiene el learning rate según el documento que estoy viendo? Responde solo con el número.';
    const focus = instance._seenFocus;
    instance._entry.set_text(question);
    instance._send();
    await waitFor(() => !instance._busy, 'the diary answer', 180);
    if (!instance._reply.text.includes('0.003')) {
        const again = await daemon(instance, {op: 'chat', prompt: question, seen: focus});
        const listed = await daemon(instance, {op: 'watch', action: 'seen', args: {}});
        throw new Error('The answer must come from the diary and its document: ' + instance._reply.text + ' ' +
            JSON.stringify({focus, again: {result: again.result, tools: again.tools, steps: again.steps,
                context: String(again.action_status ?? '').slice(0, 1500)}, entries: listed.entries}));
    }
    instance._bubble.close(0);
    await Scripting.sleep(300);
    if (instance._seenFocus) throw new Error('Closing the balloon forgets the chosen entry');
    print(`LEO_SEEN_PASS: diario de hoy en el globo («${listed}»), entrada elegida y respuesta con su documento: «${instance._reply.text.slice(0, 80)}»`);
    instance._detach();  // As if dragged out onto the desktop.
    instance._set({roam: true, motion: true});
    await waitFor(() => instance._watchSent === 'all', 'watching everything while walking');
    let status = await daemon(instance, {op: 'watch', action: 'status', args: {}});
    if (status.watching !== 'all') throw new Error('Walking Leo must watch the whole desktop: ' + JSON.stringify(status));
    instance._set({roam: false});
    await waitFor(() => instance._watchSent === 'off' && !instance._eye.visible, 'still Leo not watching');
    status = await daemon(instance, {op: 'watch', action: 'status', args: {}});
    if (status.watching !== null) throw new Error('Still Leo must not watch: ' + JSON.stringify(status));
    instance._set({watch: false});
    app.force_exit();
    print('LEO_WATCH_POSTURE_PASS: paseando observa todo; quieto no observa; el ojo lo indica');
}

export async function run() {
    await Scripting.sleep(1800);
    Main.overview.hide();
    await Scripting.sleep(500);
    await Scripting.createTestWindow({width: 500, height: 300});
    await Scripting.waitTestWindows();
    // The perf helper quits after an idle timeout; this run outlasts it and its final Exit call then fails.
    GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => {
        Scripting.waitTestWindows().catch(() => {});
        return GLib.SOURCE_CONTINUE;
    });
    const extension = Main.extensionManager.lookup('leo@local.desktop');
    if (extension?.state !== 1) throw new Error('Extension not enabled: ' + extension?.error);
    const instance = extension.stateObj;
    if (!await callerDenied('Capture', null) ||
        !await callerDenied('CaptureScope', new GLib.Variant('(s)', [JSON.stringify({scope: 'all', protected: []})])))
        throw new Error('A caller other than the daemon obtained a capture');
    print('LEO_CAPTURE_AUTH_PASS: otro cliente D-Bus no puede obtener la captura');
    const card = new St.Label({text: 'PRUEBA VISUAL\nCODIGO 7391',
        style: 'font-size: 96px; color: #000000; background-color: #ffffff; padding: 48px;'});
    card.set_position(120, 160);
    Main.layoutManager.addChrome(card);
    // Record where Leo is sent to point, before its pose timer clears the target.
    const pointed = [];
    const pointRect = instance._pointRect.bind(instance);
    instance._pointRect = item => {
        const rect = pointRect(item);
        if (rect) pointed.push({space: item.space, ...rect});
        return rect;
    };
    instance._open();
    await Scripting.sleep(400);
    const focused = global.stage.get_key_focus();
    // The working window the extension captures in window scope (same rule as capture.js).
    const workspace = global.workspace_manager.get_active_workspace();
    const usable = w => w && !w.minimized && w.located_on_workspace(workspace) &&
        w.get_gtk_application_id() !== 'io.github.leo.Companion.UI' &&
        [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG].includes(w.get_window_type());
    const target = usable(global.display.focus_window) ? global.display.focus_window : global.display.sort_windows_by_stacking(
        global.get_window_actors().map(actor => actor.meta_window)).reverse().find(usable);
    for (let i = 0; i < 50 && target.get_frame_rect().width === 0; i++)
        await Scripting.sleep(100);  // The test window may still be getting its first size.
    const frame = target.get_frame_rect();
    if (frame.width === 0) throw new Error('Test window never got a size: ' + target.get_title());
    const geometry = global.display.get_monitor_geometry(target.get_monitor());
    const ids = [target.get_gtk_application_id(), target.get_sandboxed_app_id(), target.get_wm_class(),
        target.get_wm_class_instance()].filter(Boolean).map(id => id.toLowerCase())
        .filter(id => /^[a-z0-9._-]{1,128}$/.test(id));
    if (!ids.length) throw new Error('Test window without a usable identifier');
    await probe(JSON.stringify({window: [frame.x, frame.y, frame.width, frame.height],
        monitor: [geometry.x, geometry.y, geometry.width, geometry.height], ids}));
    const [cx, cy] = card.get_transformed_position();
    const [cw, ch] = card.get_transformed_size();
    const hit = pointed.find(rect => rect.space === 'stage' &&
        rect.x + rect.width / 2 >= cx - 40 && rect.x + rect.width / 2 <= cx + cw + 40 &&
        rect.y + rect.height / 2 >= cy - 40 && rect.y + rect.height / 2 <= cy + ch + 40);
    if (!hit) throw new Error('Leo must point at the card in stage coordinates: ' + JSON.stringify({pointed, card: [cx, cy, cw, ch]}));
    print(`LEO_POINT_STAGE_PASS: Leo señala el texto en coordenadas reales (${Math.round(hit.x)}, ${Math.round(hit.y)})`);
    if (!instance._bubble.isOpen || !instance._bubble.actor.visible || global.stage.get_key_focus() !== focused)
        throw new Error('Capture must restore chat visibility and preserve keyboard focus: ' + JSON.stringify({
            open: instance._bubble.isOpen, visible: instance._bubble.actor.visible,
            sameFocus: global.stage.get_key_focus() === focused,
            focus: String(global.stage.get_key_focus()), original: String(focused),
        }));
    instance._bubble.close(0);
    global.stage.set_key_focus(null);
    await Scripting.sleep(200);
    await petsAndWatch(instance);
    const dialog = new ModalDialog.ModalDialog({destroyOnClose: true});
    dialog.contentLayout.add_child(new St.Label({text: 'Diálogo de contraseña de prueba'}));
    dialog.open();
    await Scripting.sleep(400);
    await probe('modal');
    dialog.close();
    await Scripting.sleep(400);
    await lookGestures(instance);
    Main.layoutManager.removeChrome(card);
    card.destroy();
    await Main.extensionManager.disableExtension('leo@local.desktop');
    await Scripting.sleep(200);
    print('LEO_SHELL_PASS: captura sin intervención por ámbitos, ventanas protegidas, visión y chat reales, foco conservado; escritorio aislado');
}
