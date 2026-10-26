// Runs only inside gnome-shell-test-tool's isolated Wayland compositor.
import Gio from 'gi://Gio';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};
function assert(value, message) { if (!value) throw new Error(message); }

export async function run() {
    const uuid = 'leo@local.desktop';
    await Scripting.sleep(1800);
    Main.overview.hide();
    await Scripting.sleep(1000);
    const extension = Main.extensionManager.lookup(uuid);
    assert(extension, 'Extension not loaded');
    assert(extension.state === 1, `Extension not enabled: ${extension.state}; ${extension.error}`);
    let instance = extension.stateObj;
    assert(instance._pet && instance._pet.visible, 'Pet not visible on empty desktop');
    assert(instance._image.gicon, 'Sprite not loaded');
    assert(!instance._pet.can_focus, 'Pet must not take keyboard focus');
    assert(global.stage.get_key_focus() !== instance._pet, 'Idle pet must not steal focus');
    assert(Main.layoutManager.monitors.length === 2, 'Two real virtual outputs expected');
    print('LEO_MONITORS: ' + JSON.stringify(instance._areas().map(a => ({x: a.x, y: a.y, width: a.width, height: a.height}))));
    const pointer = global.stage.get_context().get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const movePointer = (x, y) => pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
    const press = state => pointer.notify_button(GLib.get_monotonic_time(), 1, state);
    async function dragTo(x, y, cancel = false) {
        const startX = instance._pet.x + 84, startY = instance._pet.y + 84;
        movePointer(startX, startY);
        await Scripting.sleep(150);
        press(Clutter.ButtonState.PRESSED);
        await Scripting.sleep(100);
        movePointer(startX + 24, startY + 12);
        await Scripting.sleep(150);
        assert(instance._drag, 'Native pointer gesture must start dragging');
        movePointer(x + 84, y + 84);
        await Scripting.sleep(150);
        if (cancel) instance._finishDrag(true);
        press(Clutter.ButtonState.RELEASED);
        await Scripting.sleep(300);
        assert(!instance._drag && !instance._dragGrab, 'Release must clear drag and modal grab');
        assert(!instance._bubble.isOpen, 'Drag must not open chat');
    }
    const parent = instance._pet.get_parent();
    const petCount = () => parent.get_children().filter(a => a.has_style_class_name?.('leo-pet')).length;
    assert(petCount() === 1, 'Exactly one pet expected');
    instance._set({focus: true});
    assert(!instance._pet.visible, 'Focus mode must hide pet');
    instance._set({focus: false, motion: false});
    const phase = instance._phase;
    await Scripting.sleep(1000);
    assert(instance._phase === phase, 'Motion disabled must freeze animation');
    instance._set({motion: true});
    const settings = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    settings.set_boolean('enable-animations', false);
    await Scripting.sleep(100);
    const reduced = instance._phase;
    await Scripting.sleep(1000);
    assert(instance._phase === reduced, 'Reduce Motion must freeze animation');
    settings.set_boolean('enable-animations', true);
    Main.overview.show();
    await Scripting.sleep(500);
    assert(!instance._pet.visible, 'Overview must hide pet');
    Main.overview.hide();
    await Scripting.sleep(700);
    // Real motion across both virtual outputs, with a deterministic destination.
    const areas = instance._areas();
    const leftIndex = areas[0].x < areas[1].x ? 0 : 1, rightIndex = 1 - leftIndex;
    const leftArea = areas[leftIndex], rightArea = areas[rightIndex];
    const crossY = (Math.max(leftArea.y, rightArea.y) + Math.min(leftArea.y + leftArea.height, rightArea.y + rightArea.height)) / 2 - 84;
    instance._pet.set_position(rightArea.x - 168 - 12, crossY);
    movePointer(leftArea.x + 10, leftArea.y + 10);
    instance._set({roam: true, pace: 'lively'});
    instance._route = [{x: rightArea.x - 84, y: crossY}, {x: rightArea.x + 12, y: crossY}];
    instance._restUntil = 0;
    const startX = instance._pet.x;
    await Scripting.sleep(500);
    assert(instance._pet.x > startX + 10 && instance._lastFrame.startsWith('walk-'), 'Free roaming must actually walk');
    const walkingFrame = instance._lastFrame;
    await Scripting.sleep(150);
    assert(instance._lastFrame !== walkingFrame, 'Gait frames must advance');
    instance._set({motion: false});
    const frozenX = instance._pet.x;
    await Scripting.sleep(250);
    assert(instance._pet.x === frozenX, 'Disabled motion must stop traversal');
    instance._set({motion: true});
    for (let i = 0; i < 80 && instance._pet.x < rightArea.x + 10; i++) await Scripting.sleep(100);
    assert(instance._pet.x >= rightArea.x + 10, 'Dog must reach the second output without teleporting');
    instance._set({roam_scope: 'current'});
    instance._chooseWalk();
    assert(instance._route.at(-1).x >= rightArea.x, 'Current-screen roaming must stay on its output');
    await dragTo(leftArea.x + 110, leftArea.y + 130);
    assert(!instance._settings.roam, 'User placement must pause roaming');
    assert(Math.abs(instance._pet.x - leftArea.x - 110) < 3, 'Drag must move pet across outputs');
    const pinnedX = instance._pet.x, pinnedY = instance._pet.y;
    await dragTo(leftArea.x + 260, leftArea.y + 220, true);
    assert(instance._pet.x === pinnedX && instance._pet.y === pinnedY, 'Cancelled drag must restore location');
    print('LEO_MOVEMENT_PASS: native pointer drag, cancelled drag, 24-frame gait, two outputs, pause and scope');
    await Scripting.createTestWindow({width: 500, height: 300, textInput: true});
    await Scripting.waitTestWindows();
    // The perf helper (and its test window) quits after an idle timeout that this long run outlasts: the
    // intermittent «Fullscreen must hide pet» failure was its window already gone ("alive": false).
    GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => {
        Scripting.waitTestWindows().catch(() => {});
        return GLib.SOURCE_CONTINUE;
    });
    await Scripting.sleep(800);
    const windows = global.get_window_actors().map(a => a.meta_window);
    print('LEO_TEST_WINDOWS: ' + JSON.stringify(windows.map(w => [w.get_wm_class(), w.get_title()])));
    const testWindow = windows.find(w => w.get_wm_class()?.toLowerCase().includes('perf'));
    assert(testWindow, 'Test window must be mapped');
    Main.activateWindow(testWindow);
    for (let i = 0; i < 10 && !global.display.focus_window; i++) {
        await Scripting.sleep(200);
        testWindow.focus(global.get_current_time());
    }
    assert(global.display.focus_window, 'Test window must have focus');
    assert(instance._pet.visible, 'Clippy-style pet must remain visible over normal windows');
    assert(instance._pet.get_parent().get_children().indexOf(instance._pet) >
        instance._pet.get_parent().get_children().indexOf(global.window_group), 'Pet must be above application windows');
    const primaryArea = areas[Main.layoutManager.primaryIndex];
    testWindow.move_resize_frame(false, primaryArea.x + 250, primaryArea.y + 300, 500, 300);
    await Scripting.sleep(300);
    let windowRect = testWindow.get_frame_rect();
    await dragTo(windowRect.x + 100, windowRect.y - 158 + 10);
    assert(instance._perch?.window === testWindow, 'Drop near top edge must perch on the real window');
    assert(Math.abs(instance._pet.y + 158 - windowRect.y) < 2, 'Feet must rest on the window edge');
    const perchedX = instance._pet.x;
    testWindow.move_frame(false, windowRect.x + 60, windowRect.y + 20);
    await Scripting.sleep(300);
    assert(Math.abs(instance._pet.x - perchedX - 60) < 2, 'Perched pet must follow the window');
    testWindow.minimize();
    await Scripting.sleep(200);
    assert(!instance._pet.visible, 'Pet attached to minimized window must hide');
    testWindow.unminimize();
    await Scripting.sleep(300);
    assert(instance._pet.visible, 'Pet must return with its window');
    print('LEO_PERCH_PASS: actual window edge, move, minimize and restore');
    // Native pointer interactions must stay local to the pet and never take focus.
    const focusBeforePetting = global.stage.get_key_focus();
    movePointer(instance._pet.x + 65, instance._pet.y + 65);
    await Scripting.sleep(950);
    assert(instance._lastFrame === 'curious-left', 'Hover must wake Leo and tilt toward pointer');
    pointer.notify_button(GLib.get_monotonic_time(), 3, Clutter.ButtonState.PRESSED);
    pointer.notify_button(GLib.get_monotonic_time(), 3, Clutter.ButtonState.RELEASED);
    await Scripting.sleep(170);
    assert(instance._lastFrame.startsWith('pet-'), 'Right click must start petting animation');
    const affectionFrame = instance._lastFrame;
    await Scripting.sleep(160);
    assert(instance._lastFrame !== affectionFrame, 'Tail/head reaction must animate');
    assert(!instance._bubble.isOpen && global.stage.get_key_focus() === focusBeforePetting, 'Petting must not open chat or steal focus');
    settings.set_boolean('enable-animations', false);
    await Scripting.sleep(100);
    assert(instance._lastFrame === 'happy', 'Petting must respect Reduce Motion');
    settings.set_boolean('enable-animations', true);
    instance._affectionUntil = 0;
    instance._touchDistance = 0;
    for (let i = 0; i < 10; i++) {
        movePointer(instance._pet.x + (i % 2 ? 105 : 65), instance._pet.y + 65);
        await Scripting.sleep(80);
    }
    assert(instance._affectionUntil > GLib.get_monotonic_time(), 'Stroking head with pointer must trigger affection');
    instance._affectionUntil = 0;
    movePointer(10, 10);
    print('LEO_INTERACTION_PASS: pointer tilt, right-click petting, 8-frame affection, head strokes, no focus theft, Reduce Motion');
    instance._open();
    await Scripting.sleep(400);
    assert(instance._bubble.isOpen, 'Click must open anchored conversation');
    assert(instance._entry.contains(global.stage.get_key_focus()), 'Explicit interaction must focus message entry');
    instance._entry.set_text('Responde solo con: Hola, soy Leo.');
    instance._send();
    assert(instance._busy, 'Sending must enter thinking state');
    assert(instance._lastFrame === 'alert', 'Thinking pose must be shown');
    for (let i = 0; i < 1900 && instance._busy; i++) await Scripting.sleep(100);
    assert(!instance._busy, 'Chat request must finish');
    assert(instance._reply.text.length > 0 && instance._reply.text !== 'Estoy pensando…', 'Response must appear in balloon');
    assert(instance._status.text === 'En tu equipo · conversación local', 'Local chat must succeed');
    assert(instance._streamChunks > 0 && instance._streamId === null, 'Answer must stream into the balloon before the final reply');
    print(`LEO_STREAM_PASS: ${instance._streamChunks} chunks before final reply`);
    print('LEO_BALLOON_REPLY: ' + instance._reply.text.replaceAll('\n', ' '));
    // Closing mid-answer drops later chunks; the reply never reopens the balloon or takes focus.
    instance._entry.set_text('Cuenta del uno al cuarenta en palabras, separadas por comas.');
    instance._send();
    for (let i = 0; i < 1200 && instance._busy && instance._streamChunks === 0; i++) await Scripting.sleep(50);
    instance._bubble.close(0);
    await Scripting.sleep(200);
    const focusAfterClose = global.stage.get_key_focus();
    const shown = instance._streamChunks;
    for (let i = 0; i < 1900 && instance._busy; i++) await Scripting.sleep(100);
    assert(!instance._busy, 'Second chat request must finish');
    assert(instance._streamChunks === shown, 'Chunks after closing the balloon must be discarded');
    assert(!instance._bubble.isOpen && global.stage.get_key_focus() === focusAfterClose, 'Late answer must not reopen balloon or steal focus');
    print('LEO_STREAM_LATE_PASS: late chunks discarded, balloon closed, focus kept');
    instance._open();
    await Scripting.sleep(400);
    if (GLib.getenv('LEO_PREVIEW_PATH')) {
        await Scripting.sleep(400);
        const screenshot = new Shell.Screenshot();
        const stream = Gio.File.new_for_path(GLib.getenv('LEO_PREVIEW_PATH')).replace(null, false, Gio.FileCreateFlags.NONE, null);
        try { await screenshot.screenshot(false, stream); } finally { stream.close(null); }
    }
    instance._bubble.close(0);
    await Scripting.sleep(200);
    assert(!instance._bubble.isOpen, 'Balloon must close and release keyboard grab');
    const initialX = instance._pet.x;
    instance._moveAside();
    await Scripting.sleep(100);
    assert(instance._pet.x !== initialX, 'Apartar must move pet to the opposite edge');
    // leoctl notify: alert pose and a short non-modal balloon; Leo walks to the project's window.
    const leoctl = args => new Promise((resolve, reject) => {
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        launcher.set_cwd(GLib.getenv('LEO_PROJECT_ROOT'));
        launcher.spawnv(['/usr/bin/python3', '-m', 'leo.cli', ...args]).wait_check_async(null, (proc, result) => {
            try { resolve(proc.wait_check_finish(result)); } catch (e) { reject(e); }
        });
    });
    const project = (testWindow.get_title() || '').split(/[^\w.-]+/).find(word => word.length >= 3) || '';
    print('LEO_NOTIFY_PROJECT: ' + project);
    const focusBeforeNotice = global.stage.get_key_focus();
    await leoctl(['notify', '--event', 'done', '--title', 'train <b>listo</b>', ...(project ? ['--project', project] : [])]);
    await Scripting.sleep(400);
    assert(instance._toast.visible && instance._toast.text === 'train ‹b›listo‹/b›', 'Notice must show a literal, non-modal balloon');
    assert(GLib.get_monotonic_time() < instance._alertUntil, 'Notice must raise the alert pose');
    assert(!instance._bubble.isOpen && global.stage.get_key_focus() === focusBeforeNotice, 'Notice must not open chat or take focus');
    if (project) {
        assert(instance._errand, 'Leo must walk toward the project window');
        for (let i = 0; i < 400 && instance._errand; i++) await Scripting.sleep(100);
        const rect = testWindow.get_frame_rect();
        assert(!instance._errand && instance._perch?.window === testWindow && Math.abs(instance._pet.y + 158 - rect.y) < 2,
            'Leo must arrive at the project window and rest on its top edge');
    }
    instance._toast.hide();
    instance._set({focus: true});
    await leoctl(['notify', '--event', 'info', '--title', 'en cola']);
    await Scripting.sleep(400);
    assert(instance._queued.length === 1 && !instance._toast.visible, 'Concentration must queue notices');
    instance._set({focus: false});
    await Scripting.sleep(300);
    assert(!instance._queued.length && instance._toast.visible && instance._toast.text === 'en cola', 'Queued notice must arrive after concentration');
    print('LEO_NOTIFY_PASS: leoctl notify → alert, literal balloon without focus, walk to project window, concentration queue');
    instance._detach();
    instance._moveAside();
    await Scripting.sleep(100);
    instance._open();
    instance._set({focus: true});
    assert(!instance._pet.visible && !instance._bubble.isOpen, 'Focus mode must hide avatar and close balloon');
    instance._set({focus: false});
    testWindow.make_fullscreen();
    await Scripting.sleep(500);
    assert(!instance._pet.visible, 'Fullscreen must hide pet: ' + JSON.stringify({alive: !!testWindow.get_compositor_private(),
        fullscreen: testWindow.is_fullscreen(), windowMonitor: testWindow.get_monitor(),
        pet: [instance._pet.x, instance._pet.y], monitors: Main.layoutManager.monitors.map((m, i) =>
            [i, m.x, m.y, m.width, m.height, global.display.get_monitor_in_fullscreen(i)])}));
    testWindow.unmake_fullscreen();
    await Scripting.destroyTestWindows();
    await Scripting.sleep(500);
    const call = payload => new Promise(resolve => instance._call(payload, resolve, {timeout: 240000}));
    assert((await call({op: 'settings', changes: {apps: true}})).ok, 'Enable apps in isolated settings');
    for (const [prompt, className] of [['Abre Leo TestOpen', 'TestOpen'], ['Abre una terminal', 'Ptyxis']]) {
        const reply = await call({op: 'chat', prompt});
        assert(reply.ok && reply.action_status, `Conversation action must succeed: ${JSON.stringify(reply)}`);
        let opened;
        for (let i = 0; i < 40; i++) {
            opened = global.get_window_actors().map(a => a.meta_window).find(w => w.get_wm_class()?.includes(className));
            if (opened) break;
            await Scripting.sleep(100);
        }
        assert(opened, `Conversation must map a real ${className} window`);
        opened.delete(global.get_current_time());
        print(`LEO_CHAT_ACTION_PASS: ${className} opened from chat; real local LLM response`);
    }
    // F2: two-step plan approved inside the balloon, then «deshaz eso». Needs a local model with tools.
    const work = `${GLib.get_home_dir()}/LeoPruebas`;
    assert(work.startsWith(GLib.getenv('XDG_RUNTIME_DIR')), 'Plan test must use the isolated HOME');
    GLib.mkdir_with_parents(work, 0o755);
    assert((await call({op: 'settings', changes: {files: true,
        folders: [{id: 'pruebas', path: work, label: 'Pruebas', write: true}]}})).ok, 'Authorize test folder');
    async function planned(prompt) {
        if (!instance._bubble.isOpen) instance._open();  // _open() toggles: a balloon left open must stay open.
        await Scripting.sleep(300);
        instance._entry.set_text(prompt);
        instance._send();
        let approvals = 0;
        for (let i = 0; i < 3000 && instance._busy; i++) {
            if (instance._approval) {
                assert(instance._bubble.isOpen && instance._approvalBox.visible, 'Approval card must be inside the open balloon');
                approvals++;
                instance._decide(true);
            }
            await Scripting.sleep(100);
        }
        assert(!instance._busy, 'Plan must finish');
        print(`LEO_PLAN_STEPS: ${instance._planLabel.text.replaceAll('\n', ' | ')} :: ${instance._reply.text.replaceAll('\n', ' ')}`);
        return approvals;
    }
    const exists = name => GLib.file_test(`${work}/${name}`, GLib.FileTest.EXISTS);
    const approved = await planned(`Crea un borrador llamado idea.md en la carpeta ${work} con el texto "hola" ` +
        'y después cámbiale el nombre a idea-final.md.');
    assert(approved >= 2 && exists('idea-final.md') && !exists('idea.md'), 'Two approved steps must create and rename the draft');
    print('LEO_PLAN_PASS: plan de dos pasos con aprobación en el globo');
    assert(await planned('Deshaz eso: revierte lo último que hiciste.') >= 1, 'Undo must ask for approval');
    assert(!exists('idea-final.md') && !exists('idea.md'), 'Undo must revert the whole plan');
    print('LEO_UNDO_PASS: «deshaz eso» revierte el plan (borrador a la papelera)');
    instance._bubble.close(0);
    // F3: a persistent reminder fires in the isolated shell as a non-modal notice.
    const at = GLib.DateTime.new_now_local().add_minutes(1);
    const reminder = await call({op: 'tool', name: 'reminder.create',
        args: {text: 'Revisar la rúbrica', at: at.format('%Y-%m-%dT%H:%M')}});
    assert(reminder.ok, `Reminder must be created: ${JSON.stringify(reminder)}`);
    const focusBeforeReminder = global.stage.get_key_focus();
    for (let i = 0; i < 1000 && !(instance._toast.visible && instance._toast.text.startsWith('Recordatorio')); i++)
        await Scripting.sleep(100);
    assert(instance._toast.text === 'Recordatorio: Revisar la rúbrica', `Reminder must fire: ${instance._toast.text}`);
    assert(!instance._bubble.isOpen && global.stage.get_key_focus() === focusBeforeReminder, 'Reminder must not take focus');
    print('LEO_REMINDER_PASS: recordatorio persistente disparado en el shell aislado sin tomar el foco');
    // F4: point at a 48×48 button by its accessible name; a real click on the pointed spot must hit it
    // (error < 24 px per axis). Then press it through AT-SPI with approval.
    const marker = `${GLib.getenv('XDG_RUNTIME_DIR')}/leo-a11y-clicked`;
    const a11yLauncher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
    a11yLauncher.unsetenv('GTK_A11Y');
    a11yLauncher.setenv('LEO_A11Y_CLICKED', marker, true);
    a11yLauncher.set_cwd(GLib.getenv('LEO_PROJECT_ROOT'));
    const a11yApp = a11yLauncher.spawnv(['/usr/bin/python3', 'tests/a11y_app.py']);
    let a11yWindow;
    for (let i = 0; i < 100 && !a11yWindow; i++) {
        await Scripting.sleep(100);
        a11yWindow = global.get_window_actors().map(a => a.meta_window).find(w => w.get_title() === 'Leo A11y Prueba');
    }
    assert(a11yWindow, 'Accessibility test window must map');
    a11yWindow.move_frame(false, primaryArea.x + 420, primaryArea.y + 260);
    Main.activateWindow(a11yWindow);
    await Scripting.sleep(800);
    assert((await call({op: 'settings', changes: {accessibility: true}})).ok, 'Enable accessibility');
    const lines = () => GLib.file_test(marker, GLib.FileTest.EXISTS)
        ? new TextDecoder().decode(GLib.file_get_contents(marker)[1]).trim().split('\n').length : 0;
    const asked = await call({op: 'tool', name: 'ui.point', args: {name: 'Pulsar prueba'}});
    assert(asked.ok && asked.approval && asked.details.includes('Leer la interfaz'), `First read needs consent: ${JSON.stringify(asked)}`);
    const pointed = await call({op: 'decide', token: asked.approval, approved: true});
    assert(pointed.ok, `Point must succeed: ${JSON.stringify(pointed)}`);
    for (let i = 0; i < 300 && (instance._errand || !instance._pointTarget); i++) await Scripting.sleep(100);
    const spot = instance._pointTarget;
    assert(spot && instance._lastFrame.startsWith('point-'), 'Leo must stand pointing at the element');
    const pet = {x: instance._pet.x, y: instance._pet.y};
    assert(pet.x + 168 <= spot.rect.x || pet.x >= spot.rect.x + spot.rect.width || pet.y >= spot.rect.y + spot.rect.height,
        'Leo must not cover the element he points at');
    print(`LEO_POINT_SPOT: ${JSON.stringify(spot)}`);
    movePointer(spot.x, spot.y);
    await Scripting.sleep(200);
    press(Clutter.ButtonState.PRESSED);
    press(Clutter.ButtonState.RELEASED);
    await Scripting.sleep(500);
    assert(lines() === 1, 'A click on the pointed spot must hit the 48×48 button (error < 24 px)');
    print('LEO_POINT_PASS: Leo señala el botón por nombre; un clic real en ese punto lo pulsa (error < 24 px)');
    const pressing = await call({op: 'tool', name: 'ui.press', args: {name: 'Pulsar prueba'}});
    assert(pressing.ok && pressing.approval, 'Pressing needs approval');
    assert((await call({op: 'decide', token: pressing.approval, approved: true})).ok, 'Approved press');
    await Scripting.sleep(500);
    assert(lines() === 2, 'Approved do_action must change the test app state');
    print('LEO_PRESS_PASS: do_action aprobado cambia el estado de la app de prueba');
    a11yApp.force_exit();
    for (let i = 0; i < 2; i++) {
        Main.extensionManager.disableExtension(uuid);
        await Scripting.sleep(500);
        assert(instance._pet === null, 'Pet must be destroyed');
        assert(instance._bubble === null, 'Balloon must be destroyed');
        assert(instance._signals.length === 0, 'Signals must be disconnected');
        assert(petCount() === 0, 'Pet actor leak after disable');
        Main.extensionManager.enableExtension(uuid);
        await Scripting.sleep(800);
        instance = Main.extensionManager.lookup(uuid).stateObj;
        assert(instance._pet, 'Re-enable failed');
        assert(instance._terminalTitle.get_style()?.includes('font-size'), 'Terminal theme must reapply after re-enable');
        assert(petCount() === 1, 'Duplicate pet actor after re-enable');
    }
    const ui = Gio.Subprocess.new(['/usr/bin/python3', GLib.getenv('LEO_PROJECT_ROOT') + '/tests/smoke_ui.py'],
        Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE);
    const ready = Gio.File.new_for_path(GLib.getenv('XDG_RUNTIME_DIR') + '/leo-ui-ready');
    // The panel first checks «Uso de IA» (F7.3), whose first reading runs in the background.
    for (let i = 0; i < 600 && !ready.query_exists(null); i++) await Scripting.sleep(100);
    if (!ready.query_exists(null)) {
        ui.force_exit();
        const [, output] = await new Promise(resolve => ui.communicate_utf8_async(null, null,
            (proc, result) => resolve(proc.communicate_utf8_finish(result).slice(1))));
        assert(false, 'Settings UI must become ready: ' + String(output).slice(-1500));
    }
    await Scripting.sleep(500);
    const uiWindow = global.get_window_actors().map(a => a.meta_window).find(w => w.get_wm_class()?.includes('Companion.UI'));
    assert(uiWindow, 'Settings window must be mapped in isolated compositor');
    const uiRect = uiWindow.get_frame_rect();
    const uiStream = Gio.File.new_for_path(GLib.getenv('LEO_UI_PREVIEW_PATH')).replace(null, false, Gio.FileCreateFlags.NONE, null);
    try { await new Shell.Screenshot().screenshot_area(uiRect.x, uiRect.y, uiRect.width, uiRect.height, uiStream); }
    finally { uiStream.close(null); }
    for (const page of ['chat', 'usage', 'mail']) {
        const requestPath = GLib.getenv('XDG_RUNTIME_DIR') + '/leo-ui-page';
        const readyPath = GLib.getenv('XDG_RUNTIME_DIR') + '/leo-ui-page-ready';
        GLib.file_set_contents(requestPath, page);
        let pageReady = false;
        for (let i = 0; i < 100; i++) {
            if (Gio.File.new_for_path(readyPath).query_exists(null)) {
                const [ok, content] = GLib.file_get_contents(readyPath);
                if (ok && new TextDecoder().decode(content) === page) { pageReady = true; break; }
            }
            await Scripting.sleep(100);
        }
        assert(pageReady, `GTK page ${page} must open`);
        await Scripting.sleep(500);
        const rect = uiWindow.get_frame_rect();
        const path = GLib.getenv('LEO_PROJECT_ROOT') + `/assets/leo/${page}-preview.png`;
        const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.NONE, null);
        try { await new Shell.Screenshot().screenshot_area(rect.x, rect.y, rect.width, rect.height, stream); }
        finally { stream.close(null); }
    }
    GLib.file_set_contents(GLib.getenv('XDG_RUNTIME_DIR') + '/leo-ui-page', 'close');
    const uiResult = await new Promise((resolve, reject) => ui.communicate_utf8_async(null, null, (proc, result) => {
        try { resolve(proc.communicate_utf8_finish(result)); } catch (error) { reject(error); }
    }));
    print('LEO_UI_RESULT: ' + uiResult[1]);
    assert(ui.get_successful(), 'Real GTK settings test must pass');
    print('LEO_SHELL_PASS: GNOME 50 Wayland; foreground avatar, anchored balloon + local response, notices, native drag, window perch, two-output roaming, local-only chat, GTK settings, cleanup/re-enable');
}
