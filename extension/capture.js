// Captura nativa por petición: PNG en memoria, sin selector, accesible solo al daemon de LEO.
// F6.1: ámbito ventana, monitor o escritorio completo, y ventanas protegidas ocultas en la captura.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Screenshot from 'resource:///org/gnome/shell/ui/screenshot.js';

const XML = `<node><interface name="io.github.leo.Capture">
<method name="Capture"><arg name="png" type="ay" direction="out"/></method>
<method name="CaptureScope"><arg name="options" type="s" direction="in"/>
<arg name="png" type="ay" direction="out"/><arg name="info" type="s" direction="out"/></method>
<method name="FocusWindow"><arg name="info" type="s" direction="out"/></method>
</interface></node>`;
const LEO_APP = 'io.github.leo.Companion.UI';
const SCOPES = new Set(['window', 'monitor', 'all', 'region', 'perched']);
const KINDS = [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG];

export function identities(window) {
    const app = Shell.WindowTracker.get_default().get_window_app(window);
    return [window.get_gtk_application_id(), window.get_sandboxed_app_id(), window.get_wm_class(),
        window.get_wm_class_instance(), app?.get_id()?.replace(/\.desktop$/, '')]
        .filter(Boolean).map(id => id.toLowerCase());
}

function parseOptions(raw) {
    const options = JSON.parse(raw);
    if (!SCOPES.has(options?.scope))
        throw new Error('Ámbito de captura inválido');
    const list = Array.isArray(options.protected) ? options.protected : [];
    if (list.length > 64 || list.some(id => typeof id !== 'string' || id.length > 128))
        throw new Error('Lista de ventanas protegidas inválida');
    const rect = options.rect;
    if ((options.scope === 'region') !== Array.isArray(rect) ||
        (rect && (rect.length !== 4 || !rect.every(Number.isInteger) || rect[2] < 8 || rect[3] < 8)))
        throw new Error('Región inválida');
    // quiet: Leo atento's periodic look — no flash and nothing of Leo hidden (no flicker); his areas are reported
    // as `mask` so the daemon blanks them in memory before analysing.
    // own (F6.7): a window scope captured from the window's own content, so nothing floating over it (Leo, his
    // balloon) covers what the vision model must see; like perched, without flash or masks.
    return {scope: options.scope, rect, quiet: options.quiet === true, own: options.own === true,
        protected: new Set(list.map(id => id.toLowerCase()))};
}

function clip(rect) {
    const x = Math.max(0, Math.round(rect.x)), y = Math.max(0, Math.round(rect.y));
    const right = Math.min(global.stage.width, Math.round(rect.x + rect.width));
    const bottom = Math.min(global.stage.height, Math.round(rect.y + rect.height));
    if (right - x < 8 || bottom - y < 8)
        throw new Error('La zona a capturar no está visible');
    return {x, y, width: right - x, height: bottom - y};
}

export class CaptureService {
    constructor(owner) {
        this.owner = owner;
        this.active = true;
        this.busy = false;
        this.captures = 0;  // Successful captures, so tests can check that a gesture captured exactly once.
        this.quiet = 0;     // Of them, Leo atento's silent ones.
        this.flashes = 0;
        this.hidden = [];
        this.cancel = new Gio.Cancellable();
        this.object = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this.object.export(Gio.DBus.session, '/io/github/leo/Capture');
    }

    // Anterior a F6.1: escritorio completo. Se conserva para daemons sin el método con ámbito.
    async CaptureAsync(_parameters, invocation) {
        try {
            const [data] = await this._capture(invocation, {scope: 'all', protected: new Set()});
            invocation.return_value(new GLib.Variant('(ay)', [data]));
        } catch (error) {
            invocation.return_dbus_error('io.github.leo.Capture.Error', error.message);
        }
    }

    async CaptureScopeAsync([raw], invocation) {
        try {
            const [data, info] = await this._capture(invocation, parseOptions(raw));
            invocation.return_value(new GLib.Variant('(ays)', [data, JSON.stringify(info)]));
        } catch (error) {
            invocation.return_dbus_error('io.github.leo.Capture.Error', error.message);
        }
    }

    // F4: the user's working window for AT-SPI (GTK 4 on Wayland does not mark its frame ACTIVE). Daemon only.
    async FocusWindowAsync(_parameters, invocation) {
        try {
            await this._requireDaemon(invocation);
            const window = this._target();
            if (!window) throw new Error('No hay una ventana de trabajo activa');
            const app = Shell.WindowTracker.get_default().get_window_app(window);
            const [frame, buffer] = [window.get_frame_rect(), window.get_buffer_rect()];
            // Both rectangles: AT-SPI window coordinates start at the buffer, a window capture at the frame (F6.7).
            const info = {pid: window.get_pid(), title: window.get_title() ?? '', app: app?.get_name() ?? null,
                ids: identities(window), frame: [frame.x, frame.y, frame.width, frame.height],
                buffer: [buffer.x, buffer.y, buffer.width, buffer.height]};
            invocation.return_value(new GLib.Variant('(s)', [JSON.stringify(info)]));
        } catch (error) {
            invocation.return_dbus_error('io.github.leo.Capture.Error', error.message);
        }
    }

    async _requireDaemon(invocation) {
        const daemon = await new Promise((resolve, reject) => {
            Gio.DBus.session.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'GetNameOwner', new GLib.Variant('(s)', ['io.github.leo.Companion']),
                new GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 3000, this.cancel,
                (bus, result) => {
                    try { resolve(bus.call_finish(result).deepUnpack()[0]); } catch (error) { reject(error); }
                });
        });
        if (invocation.get_sender() !== daemon)
            throw new Error('Solo el servicio LEO puede pedir esto');
    }

    _isLeo(window) {
        return window.get_gtk_application_id() === LEO_APP;
    }

    _target() {
        // The user's working window: the focused one, or the topmost usable one if Leo's panel has focus.
        const workspace = global.workspace_manager.get_active_workspace();
        const usable = w => w && !w.minimized && !this._isLeo(w) && w.located_on_workspace(workspace) &&
            KINDS.includes(w.get_window_type());
        const focus = global.display.focus_window;
        if (usable(focus)) return focus;
        return global.display.sort_windows_by_stacking(global.get_window_actors().map(a => a.meta_window))
            .reverse().find(usable) ?? null;
    }

    _region(options) {
        if (options.scope === 'all')
            return {scope: 'all', rect: {x: 0, y: 0, width: global.stage.width, height: global.stage.height}, app: null};
        if (options.scope === 'perched') {  // Leo atento inside a window: only that window.
            const window = this.owner._perch?.window;
            if (!window || window.minimized)
                throw new Error('Leo no está dentro de una ventana');
            if (identities(window).some(id => options.protected.has(id)))
                throw new Error('Esa ventana está protegida: Leo no la observa');
            const app = Shell.WindowTracker.get_default().get_window_app(window);
            return {scope: 'perched', rect: clip(window.get_frame_rect()), app: app?.get_name() ?? null, window};
        }
        if (options.scope === 'region') {
            const [x, y, width, height] = options.rect;
            return {scope: 'region', rect: clip({x, y, width, height}), app: null};
        }
        const target = this._target();
        if (options.scope === 'window' && target) {
            if (identities(target).some(id => options.protected.has(id)))
                throw new Error('La ventana activa está protegida: Leo no la captura');
            const app = Shell.WindowTracker.get_default().get_window_app(target);
            return {scope: 'window', rect: clip(target.get_frame_rect()), app: app?.get_name() ?? null, window: target};
        }
        const index = target ? target.get_monitor() : global.display.get_current_monitor();
        return {scope: 'monitor', rect: clip(global.display.get_monitor_geometry(index)), app: null,
            note: options.scope === 'window' ? 'No había una ventana de trabajo: capturé el monitor.' : undefined};
    }

    async _capture(invocation, options) {
        let hidden = [], stream = null;
        let acquired = false;
        try {
            await this._requireDaemon(invocation);
            if (!this.active || Main.sessionMode.isLocked || Main.sessionMode.isGreeter || Main.overview.visible)
                throw new Error('La captura no está disponible en esta vista del escritorio');
            // Polkit and keyring prompts are shell modal dialogs: never capture while one is shown.
            if (Main.layoutManager.modalDialogGroup?.get_children().some(child => child.visible))
                throw new Error('Hay un diálogo del sistema abierto (contraseña o permisos): no capturo mientras esté visible');
            if (this.busy)
                throw new Error('Ya hay una captura en curso');
            const region = this._region(options);
            this.busy = acquired = true;
            this.owner._capturing = true;
            const leo = [this.owner._pet, this.owner._bubble?.actor, this.owner._toast, ...(this.owner._decorations?.() ?? []),
                ...global.get_window_actors().filter(actor => this._isLeo(actor.meta_window))].filter(actor => actor?.visible);
            const protectedWindows = global.get_window_actors().filter(actor =>
                identities(actor.meta_window).some(id => options.protected.has(id)));
            // Inside a window, Leo captures that window's own content: nothing floats over it, nothing to hide or mask.
            const own = region.scope === 'perched' || (region.scope === 'window' && options.own);
            const mask = options.quiet && !own ? leo.map(actor => {
                const [ax, ay] = actor.get_transformed_position();
                const [aw, ah] = actor.get_transformed_size();
                return [Math.floor(ax), Math.floor(ay), Math.ceil(aw), Math.ceil(ah)];
            }) : [];
            hidden = (own ? [] : [...(options.quiet ? [] : leo), ...protectedWindows]).filter(actor => actor?.visible).map(actor => {
                    const item = {actor, opacity: actor.opacity, destroyed: false};
                    item.signal = actor.connect('destroy', () => { item.destroyed = true; });
                    return item;
                });
            this.hidden = hidden;
            // Opacidad en vez de desmapear: el globo conserva su foco y no se cierra.
            hidden.forEach(({actor}) => { actor.opacity = 0; });
            // Dejar que el compositor pinte las ventanas que estaban debajo del chat.
            if (hidden.length) await new Promise(resolve => {
                this.wake = resolve;
                this.timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 60, () => {
                    this.timer = 0;
                    this.wake = null;
                    resolve();
                    return GLib.SOURCE_REMOVE;
                });
            });
            if (!this.active || Main.sessionMode.isLocked || Main.sessionMode.isGreeter)
                throw new Error('Captura cancelada');
            stream = Gio.MemoryOutputStream.new_resizable();
            const {x, y, width, height} = region.rect;
            if (own) {
                const actor = region.window.get_compositor_private();
                if (!actor) throw new Error('La ventana ya no está disponible');
                const texture = actor.paint_to_content(null).get_texture();
                const frame = region.window.get_frame_rect(), buffer = region.window.get_buffer_rect();
                const scale = actor.get_resource_scale();
                await Shell.Screenshot.composite_to_stream(texture, Math.round((frame.x - buffer.x) * scale),
                    Math.round((frame.y - buffer.y) * scale), Math.round(frame.width * scale), Math.round(frame.height * scale),
                    1, null, 0, 0, 1, stream);
            } else if (region.scope === 'all') {
                await new Shell.Screenshot().screenshot(false, stream);
            } else {
                await new Shell.Screenshot().screenshot_area(x, y, width, height, stream);
            }
            stream.close(null);
            const data = stream.steal_as_bytes().get_data();
            if (!this.active || Main.sessionMode.isLocked || data.length > 30 * 1024 * 1024)
                throw new Error('Captura cancelada o demasiado grande');
            const info = {scope: region.scope, rect: [x, y, width, height], app: region.app};
            if (region.note) info.note = region.note;
            if (mask.length) info.mask = mask;
            this.captures++;
            // Gestures flash the taken area once Leo's actors are back; Leo atento's periodic looks never do.
            if (!options.quiet) this._flash = {x, y, width, height};
            else this.quiet++;
            return [data, info];
        } finally {
            if (stream && !stream.is_closed()) stream.close(null);
            if (acquired) {
                this.busy = false;
                this._restore();
                if (this._flash && this.active) {
                    try {
                        new Screenshot.Flashspot(this._flash).fire();
                        this.flashes++;
                    } catch (_) { /* Visual cue only. */ }
                }
                this._flash = null;
                if (this.active) {
                    this.owner._capturing = false;
                    this.owner._visibility();
                }
            }
        }
    }

    _restore() {
        for (const item of this.hidden) {
            if (item.destroyed) continue;
            item.actor.disconnect(item.signal);
            item.actor.opacity = item.opacity;
        }
        this.hidden = [];
    }

    destroy() {
        this.active = false;
        this._restore();
        this.cancel.cancel();
        if (this.timer) GLib.Source.remove(this.timer);
        this.timer = 0;
        this.wake?.();
        this.wake = null;
        this.object.unexport();
    }
}
