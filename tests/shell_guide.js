// F6.7 in the isolated GNOME session, with the real local models: a three-step guide verified after each real click,
// and the numbered-marks battery (icons whose accessible names say nothing about the picture).
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};

// Icon → what a person would say; names from tests/marks_app.py (seeded shuffle, «Acción N»).
const BATTERY = [['document-print', 'el icono de la impresora'], ['user-trash', 'la papelera'], ['system-search', 'la lupa'],
    ['emblem-system', 'el engranaje'], ['go-home', 'la casa'], ['mail-unread', 'el sobre'], ['starred', 'la estrella'],
    ['camera-photo', 'la cámara de fotos'], ['x-office-calendar', 'el calendario'], ['audio-x-generic', 'la nota musical'],
    ['changes-prevent', 'el candado'], ['audio-input-microphone', 'el micrófono'], ['audio-volume-high', 'el altavoz'],
    ['folder', 'la carpeta'], ['battery-full', 'la batería'], ['network-wireless-signal-excellent', 'el icono del wifi'],
    ['list-add', 'el signo más'], ['document-edit', 'el lápiz'], ['alarm', 'el despertador'],
    ['preferences-system-notifications', 'la campana']];

async function waitFor(check, what, seconds = 20) {
    for (let i = 0; i < seconds * 10; i++) {
        if (check()) return;
        await Scripting.sleep(100);
    }
    throw new Error('Timed out waiting for ' + what);
}

function daemon(instance, payload) {
    return new Promise(resolve => instance._call(payload, resolve, {timeout: 240000}));
}

function spawn(args, env = {}) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE});
    launcher.unsetenv('GTK_A11Y');  // These apps must be visible to AT-SPI.
    launcher.set_cwd(GLib.getenv('LEO_PROJECT_ROOT'));
    for (const [key, value] of Object.entries(env)) launcher.setenv(key, value, true);
    return launcher.spawnv(args);
}

async function windowTitled(title) {
    let window = null;
    await waitFor(() => (window = global.get_window_actors().map(a => a.meta_window).find(w => w.get_title() === title)),
        title, 30);
    return window;
}

// Asynchronous and without GTK: a synchronous helper importing Gtk 4 deadlocked this compositor (2026-10-23).
async function names() {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE});
    launcher.set_cwd(GLib.getenv('LEO_PROJECT_ROOT') + '/tests');
    const proc = launcher.spawnv(['/usr/bin/python3', 'marks_names.py']);
    const [out] = await new Promise(resolve => proc.communicate_utf8_async(null, null,
        (p, result) => resolve(p.communicate_utf8_finish(result).slice(1))));
    return JSON.parse(out);
}

export async function run() {
    await Scripting.sleep(1800);
    Main.overview.hide();
    await Scripting.sleep(500);
    const extension = Main.extensionManager.lookup('leo@local.desktop');
    if (extension?.state !== 1) throw new Error('Extension not enabled: ' + extension?.error);
    const instance = extension.stateObj;
    const set = await daemon(instance, {op: 'settings', changes: {accessibility: true, screen: true, watch: false}});
    if (!set.ok) throw new Error('Settings: ' + JSON.stringify(set));
    const pointer = global.stage.get_context().get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const click = async (x, y) => {
        pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
        await Scripting.sleep(200);
        pointer.notify_button(GLib.get_monotonic_time(), 1, Clutter.ButtonState.PRESSED);
        await Scripting.sleep(80);  // GTK 4 check buttons ignore a press and release with no time between them.
        pointer.notify_button(GLib.get_monotonic_time(), 1, Clutter.ButtonState.RELEASED);
        await Scripting.sleep(400);
    };
    // Every place Leo points at, with the instruction he shows.
    const points = [];
    const pointRect = instance._pointRect.bind(instance);
    instance._pointRect = item => {
        const rect = pointRect(item);
        if (rect) points.push({say: item.say ?? '', label: item.label ?? '', rect});
        return rect;
    };

    // What Leo announces (the guide's end, errors), to explain a failure.
    const notices = [];
    const announce = instance._announce.bind(instance);
    instance._announce = (item, extra) => {
        notices.push(item.title);
        return announce(item, extra);
    };

    const marksOnly = GLib.getenv('LEO_TEST_MARKS') === '1';
    if (!marksOnly) {
    // 1. «Guíame para…»: consent in the balloon, then three pointed steps, each verified after the user's real click.
    const marker = GLib.getenv('XDG_RUNTIME_DIR') + '/leo-guide-done';
    const wizard = spawn(['/usr/bin/python3', 'tests/guide_app.py'], {LEO_GUIDE_DONE: marker});
    const wizardWindow = await windowTitled('Asistente de prueba');
    wizardWindow.move_frame(true, 360, 220);
    Main.activateWindow(wizardWindow);
    await Scripting.sleep(800);
    instance._open();
    await Scripting.sleep(300);
    instance._entry.set_text('Leo, guíame para terminar el asistente de prueba');
    instance._send();
    await waitFor(() => instance._approvalBox.visible || !instance._busy, 'the consent to read the wizard', 60);
    if (instance._approvalBox.visible) instance._decide(true);
    await waitFor(() => !instance._busy, 'the guide to start', 90);
    if (!instance._reply.text.startsWith('Te guío'))
        throw new Error('The guide must start: ' + instance._reply.text);
    instance._bubble.close(0);
    Main.activateWindow(wizardWindow);
    const expected = ['Siguiente', 'Acepto las condiciones', 'Finalizar'];
    for (let step = 0; step < 3; step++) {
        try {
            await waitFor(() => points.length > step, `guide step ${step + 1}`, 150);
        } catch (error) {
            throw new Error(error.message + ': ' + JSON.stringify({notices, points, toast: instance._toast.text}));
        }
        const spot = points[step];
        if (spot.label !== expected[step] || !spot.say)
            throw new Error(`Step ${step + 1} must point at «${expected[step]}» with an instruction: ` + JSON.stringify(spot));
        await Scripting.sleep(600);
        await click(spot.rect.x + spot.rect.width / 2, spot.rect.y + spot.rect.height / 2);
    }
    await waitFor(() => GLib.file_test(marker, GLib.FileTest.EXISTS), 'the wizard to finish', 30);
    try {
        await waitFor(() => notices.some(text => text.startsWith('Listo')), 'the guide to verify the end', 120);
    } catch (error) {
        throw new Error(error.message + ': ' + JSON.stringify({notices, points: points.map(p => [p.label, p.say]),
            toast: instance._toast.text}));
    }
    print(`LEO_GUIDE_PASS: tres pasos señalados con instrucción («${points.map(p => p.say).join('» → «')}»), ` +
        `cada uno comprobado tras un clic real; final verificado: «${notices.find(text => text.startsWith('Listo'))}»`);
    wizard.force_exit();
    print('LEO_SHELL_PASS: guía verificada en GNOME aislado');
    return;
    }

    // 2. Numbered marks: «señálame la papelera» among 23 icons named «Acción N».
    const labels = await names();
    const app = spawn(['/usr/bin/python3', 'tests/marks_app.py']);
    const marksWindow = await windowTitled('Leo Marcas Prueba');
    marksWindow.move_frame(true, 300, 160);
    Main.activateWindow(marksWindow);
    await Scripting.sleep(1200);
    const results = [];
    for (const [icon, description] of BATTERY) {
        Main.activateWindow(marksWindow);
        const started = GLib.get_monotonic_time();
        let reply = await daemon(instance, {op: 'tool', name: 'ui.point', args: {name: description}});
        if (reply.ok && reply.approval) reply = await daemon(instance, {op: 'decide', token: reply.approval, approved: true});
        const chosen = reply.ok ? reply.result?.pointed : null;
        results.push({description, expected: labels[icon], chosen, ok: chosen === labels[icon], marks: reply.result?.marks ?? null, method: reply.result?.method ?? null, opinions: reply.result?.opinions ?? null,
            seconds: Math.round((GLib.get_monotonic_time() - started) / 100000) / 10, error: reply.ok ? undefined : reply.error});
        print('LEO_MARKS_CASE: ' + JSON.stringify(results.at(-1)));
    }
    app.force_exit();
    const right = results.filter(r => r.ok).length;
    print('LEO_MARKS_RESULT: ' + JSON.stringify({right, total: results.length, results}));
    if (right / results.length < 0.95)
        throw new Error(`Numbered marks below 95 %: ${right}/${results.length}`);
    print(`LEO_MARKS_PASS: ${right}/${results.length} elementos señalados por su aspecto con marcas numeradas`);
    print('LEO_SHELL_PASS: marcas numeradas en GNOME aislado');
}
