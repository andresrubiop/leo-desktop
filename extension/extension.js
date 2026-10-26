import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Screenshot from 'resource:///org/gnome/shell/ui/screenshot.js';

import {SIZE, FEET, WALK_FRAMES, clamp, contain, nearestMonitor, route, advance} from './movement.js';
import {CaptureService, identities} from './capture.js';

const NOTICE_US = 8000000;
// Inside a window Leo walks slower and never leaves its borders unless the user drags him out (2026-10-23).
const PERCH_PACE = {slow: 14, normal: 22, lively: 32};
// Agent mascots: one per cooking session of Claude, Codex or Antigravity, a tenth of Leo's size.
const AGENT_SIZE = Math.max(18, Math.round(SIZE / 10));
// While Leo watches, a third eye opens on his forehead (user request, 2026-10-23: like Doctor Strange's, and drawn
// like his own eyes: same almond, iris, pupil and highlight, upright between skin folds).
const THIRD_EYE = Math.round(SIZE * .155);
const CANVAS = 320; // Illustrated sprites' canvas: assets/third-eye.json (scripts/third_eye.py) uses these units.
const AGENT_TOOLS = new Set(['claude', 'codex', 'antigravity']);
const POINT_US = 12000000;
const READING_TOOLS = new Set(['files.read_text', 'files.summarize', 'files.look']);
const STEP_ICONS = {running: '…', approval: '?', done: '✓', error: '✗', denied: '⊘', cancelled: '⊘'};

const BUS = 'io.github.leo.Companion';
const PATH = '/io/github/leo/Companion';

export default class LeoExtension extends Extension {
    enable() {
        this._alive = true;
        this._capturing = false;
        this._signals = [];
        this._settings = {visible: true, focus: false, motion: true, corner: 'right', roam: false, roam_scope: 'all', pace: 'normal', position: null};
        this._localOverrides = {};
        this._busy = false;
        this._streamId = null;
        this._streamText = '';
        this._streamChunks = 0;
        this._requestId = null;
        this._approval = null;
        this._queued = [];
        this._alertUntil = 0;
        this._toastUntil = 0;
        this._errand = null;
        this._errandKind = '';
        this._pointUntil = 0;
        this._pointPose = 'point-right';
        this._pointTarget = null;
        this._alertPose = 'alert';
        this._reading = false;
        this._sprites = {};
        this._jobs = 0;
        this._state = 'idle';
        this._phase = 0;
        this._route = null;
        this._walkDistance = 0;
        this._restUntil = 0;
        this._lastTime = GLib.get_monotonic_time();
        this._idleElapsed = 0;
        this._perch = null;
        this._drag = null;
        this._side = 1;
        this._lastFrame = '';
        this._affectionUntil = 0;
        this._affectionStart = 0;
        this._touchDistance = 0;
        this._pointerSample = null;
        this._cancellable = new Gio.Cancellable();
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._pet = new St.Button({style_class: 'leo-pet', can_focus: false,
            reactive: true, track_hover: true, accessible_name: 'Leo: clic para conversar, clic derecho para acariciar; arrastra para mover'});
        this._image = new St.Icon({icon_size: SIZE});
        this._image.set_pivot_point(0.5, 0.5);
        this._pet.set_child(this._image);
        this._pet.connect('destroy', () => { this._alive = false; });
        this._pet.connect('clicked', () => this._open());
        this._pet.connect('button-press-event', (_actor, event) => {
            if (event.get_button() !== 3) return Clutter.EVENT_PROPAGATE;
            this._petLeo();
            return Clutter.EVENT_STOP;
        });
        this._pet.connect('notify::hover', () => {
            this._pointerSample = null;
            this._touchDistance = 0;
            if (this._pet.hover) this._phase = 0; // Wake gently, without opening chat.
            this._tick();
        });
        this._setupDrag();
        Main.layoutManager.addChrome(this._pet, {affectsStruts: false,
            trackFullscreen: false}); // Own visibility policy must not be overwritten by LayoutManager.
        this._buildBubble();
        // Partial answers arrive as unicast Chunk signals for the request we started.
        this._chunkSignal = Gio.DBus.session.signal_subscribe(BUS, BUS, 'Chunk', PATH, null,
            Gio.DBusSignalFlags.NONE, (_connection, _sender, _path, _iface, _signal, parameters) => {
                if (this._alive) this._chunk(...parameters.deepUnpack());
            });
        // Plan steps and approval cards of our own request (unicast).
        this._planSignal = Gio.DBus.session.signal_subscribe(BUS, BUS, 'Plan', PATH, null,
            Gio.DBusSignalFlags.NONE, (_connection, _sender, _path, _iface, _signal, parameters) => {
                if (this._alive) this._plan(...parameters.deepUnpack());
            });
        // Where to walk and point: an accessible element (window coordinates) or a box found by vision.
        this._pointSignal = Gio.DBus.session.signal_subscribe(BUS, BUS, 'Point', PATH, null,
            Gio.DBusSignalFlags.NONE, (_connection, _sender, _path, _iface, _signal, parameters) => {
                if (this._alive) this._point(parameters.deepUnpack()[0]);
            });
        // Short notices from leoctl, coding agents and watched jobs (plain text, broadcast by the daemon).
        this._eventSignal = Gio.DBus.session.signal_subscribe(BUS, BUS, 'Event', PATH, null,
            Gio.DBusSignalFlags.NONE, (_connection, _sender, _path, _iface, _signal, parameters) => {
                if (this._alive) this._event(parameters.deepUnpack()[0]);
            });
        this._toast = new St.Label({style_class: 'leo-toast', visible: false, reactive: false});
        this._toast.clutter_text.set_line_wrap(true);
        this._toast.clutter_text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR);
        Main.layoutManager.addChrome(this._toast, {affectsStruts: false, trackFullscreen: false});

        this._indicator = new PanelMenu.Button(0.0, 'LEO');
        this._indicator.add_child(new St.Icon({
            gicon: new Gio.FileIcon({file: this.dir.get_child('assets').get_child('sit.svg')}),
            icon_size: 24, style_class: 'system-status-icon',
        }));
        this._indicator.menu.addAction('Conversar con Leo', () => this._open());
        this._indicator.menu.addAction('Acariciar a Leo', () => this._petLeo());
        this._roamItem = new PopupMenu.PopupSwitchMenuItem('Pasear libremente', false);
        this._roamItem.connect('toggled', (_item, value) => this._set({roam: value}));
        this._indicator.menu.addMenuItem(this._roamItem);
        this._indicator.menu.addAction('Apartar al otro lado', () => this._moveAside());
        this._indicator.menu.addAction('Ajustes y permisos', () => this._openSettings());
        this._indicator.menu.addAction('Uso de IA, skills y cuentas', () => this._openSettings('usage'));
        this._mailItem = this._indicator.menu.addAction('Correo: sin cuentas conectadas', () => this._openSettings('mail'));
        this._focusItem = new PopupMenu.PopupSwitchMenuItem('Concentración', false);
        this._focusItem.connect('toggled', (_item, value) => this._set({focus: value}));
        this._indicator.menu.addMenuItem(this._focusItem);
        this._motionItem = new PopupMenu.PopupSwitchMenuItem('Movimiento', true);
        this._motionItem.connect('toggled', (_item, value) => this._set({motion: value}));
        this._indicator.menu.addMenuItem(this._motionItem);
        this._visibleItem = new PopupMenu.PopupSwitchMenuItem('Mostrar a Leo', true);
        this._visibleItem.connect('toggled', (_item, value) => this._set({visible: value}));
        this._indicator.menu.addMenuItem(this._visibleItem);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
        this._captureService = new CaptureService(this);
        // F6.3: «Leo, mira» from a global shortcut (grab_accelerator: no GSettings schema needed), 👁 or a region.
        this._shortcut = null;
        this._shortcutAction = 0;
        this._shortcutName = null;
        this._attachment = null;
        this._attachmentTimer = this._regionTimer = 0;
        this._looking = false;
        this._watchSent = null;
        this._watchCheck = 0;
        this._titleSent = null;
        this._context = null;
        this._contextSeen = true;
        this._badge = new St.Label({text: '📁', style_class: 'leo-eye', visible: false,
            accessible_name: 'Leo reconoció el proyecto en el que trabajas; ábrelo para verlo'});
        Main.layoutManager.addChrome(this._badge, {affectsStruts: false, trackFullscreen: false});
        this._agentPets = new Map();
        this._thirdEye = this._loadThirdEye();
        this._eyeOpen = 0;
        this._eyeStacked = 0;
        this._eye = new St.Icon({gicon: new Gio.FileIcon({file: Gio.File.new_for_path(`${this.path}/third-eye.svg`)}),
            icon_size: THIRD_EYE, reactive: false, visible: false, accessible_name: 'Leo está observando'});
        this._eye.set_pivot_point(.5, .5);
        Main.layoutManager.addChrome(this._eye, {affectsStruts: false, trackFullscreen: false});
        this._watchItem = new PopupMenu.PopupSwitchMenuItem('Leo atento: observa mientras pasea', false);
        this._watchItem.connect('toggled', (_item, value) => this._set(value ? {watch: true, screen: true} : {watch: false}));
        this._indicator.menu.addMenuItem(this._watchItem, 3);
        // F7.3: the AI Usage Widget's black and white (GNOME's magnifier at 1×), kept in sync when the menu opens.
        this._grayItem = new PopupMenu.PopupSwitchMenuItem('Blanco y negro', false);
        this._grayItem.connect('toggled', (_item, value) => this._call({op: 'ai', action: 'grayscale', args: {on: value}},
            response => { if (response.ok && this._grayItem) this._grayItem.setToggleState(response.grayscale); }, {timeout: 5000}));
        this._indicator.menu.addMenuItem(this._grayItem, 4);
        this._indicator.menu.connect('open-state-changed', (_menu, open) => {
            if (open) this._call({op: 'ai', action: 'grayscale', args: {}}, response => {
                if (!this._grayItem) return;
                this._grayItem.visible = response.ok && response.available;
                if (response.ok) this._grayItem.setToggleState(response.grayscale);
            }, {timeout: 5000});
        });
        this._connect(global.display, 'accelerator-activated', (_display, action) => {
            if (action && action === this._shortcutAction) this._lookNow('window');
        });
        this._connect(Main.layoutManager, 'monitors-changed', () => this._layoutChanged());
        this._connect(global.display, 'workareas-changed', () => this._layoutChanged());
        this._connect(global.workspace_manager, 'active-workspace-changed', () => this._visibility());
        this._connect(global.display, 'notify::focus-window', () => this._visibility());
        this._connect(global.display, 'in-fullscreen-changed', () => this._visibility());
        this._connect(Main.overview, 'showing', () => this._visibility());
        this._connect(Main.overview, 'hidden', () => this._visibility());
        this._connect(Main.sessionMode, 'updated', () => this._visibility());
        this._connect(this._interface, 'changed::enable-animations', () => this._visibility());
        this._position();
        this._tick();
        this._poll(true);
        this._animation = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 33, () => {
            if (!this._alive) { this._animation = 0; return GLib.SOURCE_REMOVE; }
            this._animate();
            return GLib.SOURCE_CONTINUE;
        });
        this._poller = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
            if (!this._alive) { this._poller = 0; return GLib.SOURCE_REMOVE; }
            this._poll();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _connect(object, signal, callback) {
        this._signals.push([object, object.connect(signal, (...args) => {
            if (this._alive) callback(...args);
        })]);
    }

    _open() {
        if (!this._pet.visible || this._drag) return;
        if (this._bubble.isOpen) {
            this._bubble.close(0);
            return;
        }
        this._pet.remove_all_transitions();
        this._pet.translation_x = 0;
        this._fitBubble();
        this._contextSeen = true;
        this._loadSeen();
        this._bubble.open(0);
        // Keyboard focus is requested only by this explicit user interaction.
        this._entry.grab_key_focus();
        this._tick();
    }

    _openSettings(page = '') {
        this._bubble.close(0);
        const app = Shell.AppSystem.get_default().lookup_app('io.github.leo.Companion.desktop');
        if (app) {
            const info = app.get_app_info();
            if (page && info.list_actions().includes(page))
                info.launch_action(page, global.create_app_launch_context(0, -1));
            else
                app.activate();
        }
        else
            Main.notify('LEO', 'Instala el lanzador con scripts/install.py o ejecuta scripts/run.sh ui.');
    }

    _buildBubble() {
        this._terminalKey = null;
        this._bubble = new PopupMenu.PopupMenu(this._pet, 0.5, St.Side.BOTTOM);
        this._bubble.actor.add_style_class_name('leo-balloon');
        Main.layoutManager.addChrome(this._bubble.actor, {affectsStruts: false, trackFullscreen: false});
        this._bubble.actor.hide();
        this._bubbleManager = new PopupMenu.PopupMenuManager(this);
        this._bubbleManager.addMenu(this._bubble);
        // Once closed, the rest of a streamed answer is dropped; the final reply still lands silently.
        this._bubble.connect('open-state-changed', (_menu, open) => {
            if (open) return;
            this._streamId = null;
            // Closing the balloon cancels the plan: pending approvals are denied, no further steps run.
            if (this._busy && this._requestId) this._call({op: 'cancel', request_id: this._requestId}, () => {});
            this._showApproval(null);
            this._chooseSeen(null);
            // G11: the remembered capture never outlives the conversation.
            if (this._attachment) this._forget();
        });
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const content = new St.BoxLayout({vertical: true, style_class: 'leo-conversation', x_expand: true});
        this._conversation = content;
        item.add_child(content);
        this._bubble.addMenuItem(item);
        const header = new St.BoxLayout({style_class: 'leo-bubble-header'});
        this._terminalTitle = new St.Label({text: 'leo@local:~$', style_class: 'leo-name', x_expand: true});
        header.add_child(this._terminalTitle);
        const close = new St.Button({label: '×', style_class: 'leo-close', can_focus: true,
            accessible_name: 'Cerrar conversación'});
        close.connect('clicked', () => this._bubble.close(0));
        header.add_child(close);
        content.add_child(header);
        this._contextLabel = new St.Label({style_class: 'leo-status', visible: false, x_expand: true});
        this._contextLabel.clutter_text.set_line_wrap(true);
        this._contextLabel.clutter_text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR);
        content.add_child(this._contextLabel);
        // F7.1: what Leo saw today; choosing an entry makes it the subject of the next question.
        this._seen = [];
        this._seenFocus = null;
        this._seenExpanded = false;
        this._seenBox = new St.BoxLayout({vertical: true, style_class: 'leo-seen', visible: false});
        const seenHeader = new St.BoxLayout({style_class: 'leo-seen-header'});
        this._seenTitle = new St.Label({text: '👁 lo que vi hoy', style_class: 'leo-seen-title', x_expand: true,
            y_align: Clutter.ActorAlign.CENTER});
        this._seenMore = new St.Button({label: 'más', style_class: 'leo-link', can_focus: true,
            accessible_name: 'Ver más de lo que Leo vio hoy'});
        this._seenMore.connect('clicked', () => {
            this._seenExpanded = !this._seenExpanded;
            this._renderSeen();
        });
        seenHeader.add_child(this._seenTitle);
        seenHeader.add_child(this._seenMore);
        this._seenBox.add_child(seenHeader);
        this._seenList = new St.BoxLayout({vertical: true, style_class: 'leo-seen-list'});
        this._seenScroll = new St.ScrollView({style_class: 'leo-seen-scroll', overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC});
        this._seenScroll.set_child(this._seenList);
        this._seenBox.add_child(this._seenScroll);
        content.add_child(this._seenBox);
        this._reply = new St.Label({text: '[sistema] Conversación local con Leo.\nEscribe un mensaje para comenzar.',
            style_class: 'leo-reply', x_expand: true});
        this._reply.clutter_text.set_line_wrap(true);
        this._reply.clutter_text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR);
        this._reply.clutter_text.set_ellipsize(Pango.EllipsizeMode.NONE);
        this._reply.clutter_text.set_selectable(true);
        const replyBox = new St.BoxLayout({vertical: true});
        replyBox.add_child(this._reply);
        this._scroll = new St.ScrollView({style_class: 'leo-reply-scroll', overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC});
        this._scroll.set_child(replyBox);
        content.add_child(this._scroll);
        this._planBox = new St.BoxLayout({vertical: true, style_class: 'leo-plan', visible: false});
        this._planLabel = new St.Label({style_class: 'leo-plan-steps', x_expand: true});
        this._planLabel.clutter_text.set_line_wrap(true);
        this._planLabel.clutter_text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR);
        this._planBox.add_child(this._planLabel);
        this._approvalBox = new St.BoxLayout({vertical: true, style_class: 'leo-approval', visible: false});
        this._approvalLabel = new St.Label({style_class: 'leo-approval-text', x_expand: true});
        this._approvalLabel.clutter_text.set_line_wrap(true);
        this._approvalLabel.clutter_text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR);
        this._approvalLabel.clutter_text.set_selectable(true);
        const approvalScroll = new St.ScrollView({style_class: 'leo-approval-scroll', overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC});
        const approvalText = new St.BoxLayout({vertical: true});
        approvalText.add_child(this._approvalLabel);
        approvalScroll.set_child(approvalText);
        this._approvalBox.add_child(approvalScroll);
        const decisions = new St.BoxLayout({style_class: 'leo-bubble-actions'});
        this._approveButton = new St.Button({label: 'Aprobar una vez', style_class: 'leo-send', can_focus: true,
            accessible_name: 'Aprobar exactamente esta acción una vez'});
        this._approveButton.connect('clicked', () => this._decide(true));
        this._rejectButton = new St.Button({label: 'Cancelar', style_class: 'leo-link', can_focus: true});
        this._rejectButton.connect('clicked', () => this._decide(false));
        decisions.add_child(this._approveButton);
        decisions.add_child(this._rejectButton);
        this._approvalBox.add_child(decisions);
        this._planBox.add_child(this._approvalBox);
        content.add_child(this._planBox);
        this._attachmentBox = new St.BoxLayout({style_class: 'leo-attachment', visible: false});
        this._attachmentIcon = new St.Icon({icon_size: 56, style_class: 'leo-attachment-thumb'});
        this._attachmentLabel = new St.Label({style_class: 'leo-status', x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        this._attachmentLabel.clutter_text.set_line_wrap(true);
        const forget = new St.Button({label: 'Olvidar', style_class: 'leo-link', can_focus: true,
            y_align: Clutter.ActorAlign.CENTER, accessible_name: 'Olvidar la captura que Leo recuerda'});
        forget.connect('clicked', () => this._forget());
        this._attachmentBox.add_child(this._attachmentIcon);
        this._attachmentBox.add_child(this._attachmentLabel);
        this._attachmentBox.add_child(forget);
        content.add_child(this._attachmentBox);
        this._entry = new St.Entry({hint_text: 'tú@local:~$ escribe a Leo…', can_focus: true, x_expand: true,
            style_class: 'leo-entry', accessible_name: 'Mensaje para Leo'});
        this._entry.clutter_text.set_max_length(16000);
        this._entry.clutter_text.connect('activate', () => this._send());
        content.add_child(this._entry);
        const footer = new St.BoxLayout({style_class: 'leo-bubble-footer'});
        this._status = new St.Label({text: 'En tu equipo · conversación local', style_class: 'leo-status',
            x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        footer.add_child(this._status);
        this._lookButton = new St.Button({label: '👁', style_class: 'leo-link', can_focus: true,
            accessible_name: 'Que Leo mire la ventana activa: una captura, solo en memoria'});
        this._lookButton.connect('clicked', () => this._lookNow('window'));
        footer.add_child(this._lookButton);
        this._regionButton = new St.Button({label: '⬚', style_class: 'leo-link', can_focus: true,
            accessible_name: 'Elegir una zona de la pantalla para que Leo la mire; Esc cancela'});
        this._regionButton.connect('clicked', () => this._selectRegion());
        footer.add_child(this._regionButton);
        this._sendButton = new St.Button({label: 'Enviar', style_class: 'leo-send', can_focus: true});
        this._sendButton.connect('clicked', () => this._send());
        footer.add_child(this._sendButton);
        content.add_child(footer);
        const actions = new St.BoxLayout({style_class: 'leo-bubble-actions'});
        for (const [title, callback] of [['Apartar', () => this._moveAside()],
            ['Pasear', () => { this._bubble.close(0); this._set({roam: true, motion: true}); }],
            ['Quieto aquí', () => { this._bubble.close(0); this._pin(); if (this._perch) this._perch.walk = false; }],
            ['Ajustes', () => this._openSettings()]]) {
            const action = new St.Button({label: title, style_class: 'leo-link', can_focus: true});
            action.connect('clicked', callback);
            actions.add_child(action);
        }
        content.add_child(actions);
        const petAction = new St.Button({label: '♡ Acariciar a Leo', style_class: 'leo-link', can_focus: true,
            accessible_name: 'Acariciar a Leo; también puedes mover el puntero sobre su cabeza'});
        petAction.connect('clicked', () => { this._bubble.close(0); this._petLeo(); });
        content.add_child(petAction);
        this._bubble.actor.connect('key-press-event', (_actor, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape) {
                this._bubble.close(0);
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._terminalButtons = [close, this._sendButton, this._lookButton, this._regionButton, forget, this._seenMore,
            ...actions.get_children(), petAction, this._rejectButton];
    }

    _applyTerminal(theme) {
        if (!theme || JSON.stringify(theme) === this._terminalKey) return;
        this._terminalKey = JSON.stringify(theme);
        this._theme = theme;
        const font = Pango.FontDescription.from_string(theme.font || 'Monospace 11');
        const family = (font.get_family() || 'Monospace').replace(/[";{}]/g, '');
        const size = Math.max(6, Math.min(32, font.get_size() / Pango.SCALE || 11));
        this._conversation.set_style(`font-family: "${family}"; font-size: ${size}pt; color: ${theme.fg};`);
        this._bubble.actor.set_style(`-arrow-background-color: ${theme.bg}; -arrow-border-color: ${theme.border}; -arrow-border-radius: 10px; color: ${theme.fg};`);
        this._bubble.box.set_style(`background-color: ${theme.bg}; border-color: ${theme.border}; border-radius: 10px;`);
        this._terminalTitle.set_style(`color: ${theme.palette[10]}; font-size: ${size + 1}pt;`);
        this._reply.set_style(`color: ${theme.fg}; font-size: ${size}pt;`);
        this._status.set_style(`color: ${theme.header_fg};`);
        this._entry.set_style(`background-color: ${theme.header_bg}; color: ${theme.fg}; border-color: ${theme.border}; border-radius: 4px;`);
        for (const item of this._terminalButtons)
            item.set_style(`background-color: ${theme.header_bg}; color: ${theme.header_fg}; border-radius: 4px;`);
    }

    _send() {
        const prompt = this._entry.get_text().trim();
        if (this._busy || this._looking || !prompt) return;
        this._busy = true;
        this._sendButton.reactive = false;
        this._entry.set_text('');
        this._reply.text = 'Estoy pensando…';
        this._status.text = 'Consultando el modelo local';
        const requestId = GLib.uuid_string_random();
        this._streamId = requestId;
        this._requestId = requestId;
        this._streamText = '';
        this._streamChunks = 0;
        this._planBox.hide();
        this._showApproval(null);
        this._tick();
        const request = {op: 'chat', prompt, request_id: requestId, remember: true};
        if (this._seenFocus) request.seen = this._seenFocus;
        this._call(request, response => {
            if (response.ok && 'attached' in response) this._setAttachment(response.attached);
            if (this._streamId === requestId) this._streamId = null;
            if (this._requestId === requestId) this._requestId = null;
            this._showApproval(null);
            this._reading = false;
            if (response.ok && response.steps?.length && response.steps.every(step => step.status === 'done')) {
                this._alertPose = 'celebrate'; // A finished plan earns a short celebration.
                this._alertUntil = GLib.get_monotonic_time() + 3000000;
            }
            this._busy = false;
            this._sendButton.reactive = true;
            this._reply.text = response.ok ? String(response.result).slice(0, 16000) :
                (response.error || 'No pude responder. Revisa el servicio local en Ajustes.') +
                (response.action_status ? '\n[sistema] ' + response.action_status : '');
            this._status.text = response.ok ? (response.tools?.length ? 'Datos consultados · respuesta del LLM local' : 'En tu equipo · conversación local') : 'Sin conexión al asistente local';
            this._scroll.vadjustment.value = 0;
            this._state = 'idle';
            this._phase = 2;
            this._tick();
            // Never reopen the balloon or steal focus when the answer arrives.
        }, {autoStart: true, timeout: 600000}); // Plans may wait up to 120 s per approval.
    }

    _plan(requestId, raw) {
        if (requestId !== this._requestId) return;
        let plan;
        try {
            plan = JSON.parse(raw);
        } catch (_) {
            return;
        }
        const steps = Array.isArray(plan.steps) ? plan.steps : [];
        this._reading = steps.some(step => step.status === 'running' && READING_TOOLS.has(step.tool));
        // Literal text: tool names and short messages are never markup.
        this._planLabel.text = 'Plan de Leo:\n' + steps.map(step => `${STEP_ICONS[step.status] ?? '·'} ${step.n}. ${step.tool}` +
            (['error', 'denied'].includes(step.status) && step.message ? ` — ${String(step.message).slice(0, 160)}` : '')).join('\n');
        this._planBox.visible = steps.length > 0;
        this._showApproval(plan.approval ?? null);
    }

    _showApproval(approval) {
        this._approval = approval && typeof approval.token === 'string' ? approval : null;
        if (!this._approvalBox) return;
        this._approvalBox.visible = this._approval !== null;
        if (!this._approval) return;
        const details = this._approval.details ? `\n${this._approval.details}` : '';
        this._approvalLabel.text = `¿Aprobar? ${this._approval.description}${details}\n` +
            `${this._approval.tool} ${JSON.stringify(this._approval.arguments ?? {}, null, 2)}\n` +
            `Solo esta acción · caduca en ${this._approval.expires_in ?? 120} s`;
        this._status.text = 'Leo espera tu aprobación';
        // Never opens the balloon or moves focus: the card appears only inside an open balloon.
    }

    _decide(approved) {
        const approval = this._approval;
        if (!approval) return;
        this._showApproval(null);
        this._status.text = approved ? 'Aprobado · ejecutando' : 'Cancelado';
        this._call({op: 'decide', token: approval.token, approved}, () => {}, {timeout: 5000});
    }

    _chunk(requestId, text) {
        // Literal text only (never markup); ignored unless it belongs to the open conversation.
        if (requestId !== this._streamId || !this._bubble?.isOpen || typeof text !== 'string') return;
        this._streamText = (this._streamText + text).slice(0, 16000);
        this._streamChunks++;
        this._reply.text = this._streamText;
        this._status.text = 'Leo está escribiendo · modelo local';
    }

    _event(raw) {
        let item;
        try {
            item = JSON.parse(raw);
        } catch (_) {
            return;
        }
        if (!item || typeof item.title !== 'string') return;
        // Concentration queues notices; they are delivered when it ends.
        if (this._settings.focus) {
            this._queued = [...this._queued, item].slice(-10);
            return;
        }
        this._announce(item);
    }

    _announce(item, extra = 0) {
        const title = item.title.slice(0, 160);
        Main.notify('Leo', extra ? `${title} (+${extra} avisos más)` : title);
        // Hidden in fullscreen, overview or lock: the system notification is enough.
        if (!this._pet.visible) return;
        this._alertUntil = GLib.get_monotonic_time() + NOTICE_US;
        this._alertPose = item.event === 'done' ? 'celebrate' : 'alert';
        this._toastUntil = this._alertUntil;
        this._toast.text = title;
        this._toast.show();
        this._placeToast();
        if (typeof item.project === 'string' && item.project) this._walkToProject(item.project);
        this._tick();
    }

    _placeToast() {
        if (!this._toast?.visible) return;
        const areas = this._areas();
        if (!areas.length) return;
        const area = areas[nearestMonitor(this._pet, areas)];
        const [, width] = this._toast.get_preferred_width(-1);
        const [, height] = this._toast.get_preferred_height(width);
        const x = clamp(this._pet.x + SIZE / 2 - width / 2, area.x + 8, area.x + area.width - width - 8);
        const above = this._pet.y - height - 4;
        this._toast.set_position(Math.round(x), Math.round(above >= area.y ? above : this._pet.y + SIZE + 4));
    }

    _walkToProject(project) {
        const motion = this._settings.motion && this._interface.get_boolean('enable-animations');
        // Leo living in a window only leaves it when the user drags him out.
        if (!motion || this._drag || this._bubble.isOpen || this._perch) return;
        const needle = project.toLowerCase();
        const workspace = global.workspace_manager.get_active_workspace();
        // Best effort: the topmost normal window whose title names the project.
        const window = global.display.sort_windows_by_stacking(
            global.get_window_actors().map(actor => actor.meta_window)).reverse().find(w =>
            !w.minimized && !w.is_fullscreen() && !w.skip_taskbar && w.located_on_workspace(workspace) &&
            (w.get_title() || '').toLowerCase().includes(needle));
        const areas = this._areas();
        if (!window || !areas[window.get_monitor()]) return;
        const rect = window.get_frame_rect();
        const path = route(areas, nearestMonitor(this._pet, areas), window.get_monitor(),
            {x: rect.x + rect.width / 2 - SIZE / 2, y: rect.y - FEET});
        if (!path) return;
        this._detach();
        this._errand = path;
    }

    _pointRect(item) {
        const rect = Array.isArray(item.rect) && item.rect.length === 4 && item.rect.every(Number.isFinite) ? item.rect : null;
        if (!rect || rect[2] <= 0 || rect[3] <= 0) return null;
        if (item.space === 'window') {
            // AT-SPI window coordinates start at the frame for GTK 4 and at the buffer (shadows included) for
            // GTK 3 (measured 2026-10-23); the daemon says which with `origin`.
            const windows = global.get_window_actors().map(actor => actor.meta_window)
                .filter(w => w.get_pid() === item.pid && !w.minimized);
            const window = windows.find(w => w.has_focus()) ?? windows.find(w => w.get_title() === item.window) ?? windows[0];
            if (!window) return null;
            const start = item.origin === 'frame' ? window.get_frame_rect() : window.get_buffer_rect();
            return {x: start.x + rect[0], y: start.y + rect[1], width: rect[2], height: rect[3]};
        }
        if (item.space === 'stage')  // F6.1: the daemon already mapped the scoped capture to stage coordinates.
            return {x: rect[0], y: rect[1], width: rect[2], height: rect[3]};
        if (item.space === 'screen' && Array.isArray(item.image) && item.image[0] > 0 && item.image[1] > 0) {
            // A full-desktop capture in physical pixels; refuse partial captures instead of guessing.
            const sx = global.stage.width / item.image[0], sy = global.stage.height / item.image[1];
            if (Math.abs(sx - sy) / sx > 0.03) return null;
            return {x: rect[0] * sx, y: rect[1] * sy, width: rect[2] * sx, height: rect[3] * sy};
        }
        return null;
    }

    _point(raw) {
        let item;
        try {
            item = JSON.parse(raw);
        } catch (_) {
            return;
        }
        const label = typeof item?.label === 'string' ? item.label.slice(0, 80) : '';
        const target = item ? this._pointRect(item) : null;
        if (!target || !this._pet.visible || this._drag) {
            if (label) this._announce({title: `No pude señalar «${label}» en la pantalla`}, 0);
            return;
        }
        const areas = this._areas();
        const area = areas[nearestMonitor({x: target.x, y: target.y}, areas)];
        // Stand beside the element, never on it, on the side with room; the paw reaches its middle.
        const left = target.x - SIZE - 8 >= area.x;
        let dest = {x: left ? target.x - SIZE - 8 : target.x + target.width + 8,
            y: target.y + target.height / 2 - SIZE * 0.57};
        dest = contain(dest, area);
        const overlaps = dest.x < target.x + target.width && dest.x + SIZE > target.x &&
            dest.y < target.y + target.height && dest.y + SIZE > target.y;
        if (overlaps) dest = contain({x: dest.x, y: target.y + target.height + 8}, area);
        this._pointTarget = {x: target.x + target.width / 2, y: target.y + target.height / 2, rect: target, stand: dest};
        this._pointPose = left ? 'point-right' : 'point-left';
        this._route = null;
        const motion = this._settings.motion && this._interface.get_boolean('enable-animations');
        if (this._perch) {  // Inside a window: point from where he is, without leaving it.
            this._pointPose = this._pointTarget.x > this._pet.x + SIZE / 2 ? 'point-right' : 'point-left';
            dest = null;
        }
        const path = motion && dest ? route(areas, nearestMonitor(this._pet, areas), areas.indexOf(area), dest) : null;
        if (!dest) {
            this._pointUntil = GLib.get_monotonic_time() + POINT_US;
        } else if (path) {
            this._errand = path;
            this._errandKind = 'point';
        } else {
            this._pet.set_position(dest.x, dest.y);
        }
        if (dest) this._pointUntil = GLib.get_monotonic_time() + POINT_US + (path ? 8000000 : 0);
        // F6.7: during a guide the instruction stays beside Leo while the user does it (up to `hold` seconds).
        const say = typeof item.say === 'string' ? item.say.slice(0, 200) : '';
        const hold = say && Number.isFinite(item.hold) ? Math.min(180, Math.max(0, item.hold)) * 1000000 : 0;
        this._toastUntil = Math.max(this._pointUntil, GLib.get_monotonic_time() + hold);
        this._toast.text = say ? `${item.doubt ? '¿Es aquí? ' : ''}${say}` :
            item.doubt ? `Creo que es aquí: «${label}»` : `Aquí: «${label}»`;
        this._toast.show();
        this._placeToast();
        this._tick();
    }

    _step(target, speed) {
        const next = advance(this._pet, target, speed);
        if (Math.abs(target.x - this._pet.x) > 1) this._side = target.x > this._pet.x ? 1 : -1;
        this._pet.set_position(next.x, next.y);
        this._walkDistance += next.distance;
        this._frame(`walk-${Math.floor(this._walkDistance / 32.2 * WALK_FRAMES) % WALK_FRAMES}`);
        this._placeToast();
        return next.arrived;
    }

    _moveAside() {
        this._bubble.close(0);
        this._detach();
        this._set({corner: this._settings.corner === 'left' ? 'right' : 'left', position: null, roam: false});
    }

    _call(payload, callback, {autoStart = false, timeout = 1500} = {}) {
        const session = this._cancellable;
        Gio.DBus.session.call(BUS, PATH, BUS, 'Call', new GLib.Variant('(s)', [JSON.stringify(payload)]),
            new GLib.VariantType('(s)'), autoStart ? Gio.DBusCallFlags.NONE : Gio.DBusCallFlags.NO_AUTO_START,
            timeout, session,
            (connection, result) => {
                if (!this._alive || session !== this._cancellable) return;
                let response;
                try {
                    response = JSON.parse(connection.call_finish(result).deepUnpack()[0]);
                } catch (_) {
                    response = {ok: false, error: 'No puedo conectar con el asistente local. Instala LEO o inicia scripts/run.sh daemon.'};
                }
                callback(response);
            });
    }

    _poll(autoStart = false) {
        this._call({op: 'status'}, response => {
            if (!response.ok) return;
            const previous = this._settings;
            this._settings = {...response.settings, ...this._localOverrides};
            this._applyTerminal(response.terminal);
            const mail = response.mail || {};
            this._mailItem.label.text = mail.connected ? `Correo: ${mail.unread || 0} sin leer${mail.errors ? ' · revisa conexión' : ''}` : 'Correo: sin cuentas conectadas';
            this._state = response.state;
            this._jobs = response.jobs?.active || 0;
            this._updateAgentPets(Array.isArray(response.cooking) ? response.cooking : []);
            this._focusItem.setToggleState(this._settings.focus);
            this._motionItem.setToggleState(this._settings.motion);
            this._visibleItem.setToggleState(this._settings.visible);
            this._applySettings(previous);
            this._visibility();
            if (Object.keys(this._localOverrides).length && !this._busy) this._persistVisualSettings();
        }, {autoStart});
    }

    _set(changes) {
        // Local visual controls work even with the daemon stopped.
        const previous = {...this._settings};
        Object.assign(this._settings, changes);
        Object.assign(this._localOverrides, changes);
        this._applySettings(previous);
        this._visibility();
        if (!this._busy) this._persistVisualSettings();
    }

    _persistVisualSettings() {
        const changes = {...this._localOverrides};
        this._call({op: 'settings', changes}, response => {
            if (response.ok) {
                for (const [key, value] of Object.entries(changes))
                    if (this._localOverrides[key] === value) delete this._localOverrides[key];
            }
        });
    }

    _areas() {
        return Main.layoutManager.monitors.map((_, i) => Main.layoutManager.getWorkAreaForMonitor(i));
    }

    _applySettings(previous) {
        if (previous.focus && !this._settings.focus && this._queued.length) {
            const queued = this._queued;
            this._queued = [];
            this._visibility();
            this._announce(queued[queued.length - 1], queued.length - 1);
        }
        if (previous.roam !== this._settings.roam || previous.roam_scope !== this._settings.roam_scope) {
            this._route = null;
            this._restUntil = 0;
            if (this._settings.roam) this._detach();
        }
        if (previous.corner !== this._settings.corner ||
            JSON.stringify(previous.position) !== JSON.stringify(this._settings.position)) {
            if (!this._drag && !this._perch) this._position();
        }
        this._roamItem.setToggleState(this._settings.roam);
        this._motionItem.setToggleState(this._settings.motion);
        this._focusItem.setToggleState(this._settings.focus);
        this._visibleItem.setToggleState(this._settings.visible);
        if (this._settings.look_shortcut !== undefined) this._grabShortcut(this._settings.look_shortcut);
        this._watchItem?.setToggleState(!!this._settings.watch);
        if (previous.watch !== this._settings.watch) this._syncWatch(true);
    }

    _grabShortcut(accelerator) {
        if (accelerator === this._shortcut) return;
        this._ungrabShortcut();
        this._shortcut = accelerator;
        if (!accelerator) return;
        const action = global.display.grab_accelerator(accelerator, Meta.KeyBindingFlags.NONE);
        if (action === Meta.KeyBindingAction.NONE) {
            console.warn(`LEO: no se pudo registrar el atajo ${accelerator}; quizá ya lo usa otra función`);
            return;
        }
        this._shortcutAction = action;
        this._shortcutName = Meta.external_binding_name_for_action(action);
        Main.wm.allowKeybinding(this._shortcutName, Shell.ActionMode.NORMAL);
    }

    _ungrabShortcut() {
        if (this._shortcutAction) {
            global.display.ungrab_accelerator(this._shortcutAction);
            Main.wm.allowKeybinding(this._shortcutName, Shell.ActionMode.NONE);
        }
        this._shortcutAction = 0;
        this._shortcutName = null;
    }

    // One capture now, by an explicit gesture; Leo keeps it in memory for the next questions (G11).
    _lookNow(scope, rect = null) {
        if (this._busy || this._looking || !this._alive) return;
        if (!this._pet.visible) {
            Main.notify('LEO', 'Leo está oculto o en concentración: muéstralo para que pueda mirar.');
            return;
        }
        this._looking = true;
        this._alertPose = 'watching';
        this._alertUntil = GLib.get_monotonic_time() + 2000000;
        this._tick();
        this._call({op: 'look', action: 'attach', args: rect ? {scope, rect} : {scope}}, response => {
            this._looking = false;
            if (!this._bubble.isOpen) this._open();
            if (!response.ok) {
                this._setAttachment(null);
                this._reply.text = '[sistema] ' + (response.error || 'No pude mirar la pantalla.');
                return;
            }
            this._setAttachment(response.attached);
            this._reply.text = '[sistema] Ya miré. Pregúntame lo que quieras sobre esta captura.';
            this._entry.grab_key_focus();
        }, {autoStart: true, timeout: 20000});
    }

    async _selectRegion() {
        if (this._busy || this._looking || !this._alive) return;
        if (this._bubble.isOpen) this._bubble.close(0);
        let area = null;
        try {
            area = await new Screenshot.SelectArea().selectAsync();  // GNOME's own selector; Esc → null.
        } catch (_) {
            area = null;
        }
        if (!this._alive || !area || area.width < 8 || area.height < 8)
            return;  // Cancelled: nothing is captured.
        // Let the selector fade out before the capture.
        this._regionTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
            this._regionTimer = 0;
            if (this._alive) this._lookNow('region', [area.x, area.y, area.width, area.height]);
            return GLib.SOURCE_REMOVE;
        });
    }

    _setAttachment(info) {
        if (this._attachmentTimer) GLib.Source.remove(this._attachmentTimer);
        this._attachmentTimer = 0;
        this._attachment = info ? {...info, until: GLib.get_monotonic_time() + info.expires_in * 1000000} : null;
        if (!this._attachmentBox) return;
        if (!this._attachment) {
            this._attachmentBox.hide();
            this._attachmentIcon.gicon = null;
            return;
        }
        try {
            this._attachmentIcon.gicon = Gio.BytesIcon.new(new GLib.Bytes(GLib.base64_decode(info.thumbnail)));
        } catch (_) {
            this._attachmentIcon.gicon = null;
        }
        this._attachmentBox.show();
        const where = {window: 'ventana', monitor: 'monitor', all: 'escritorio completo', region: 'zona elegida'};
        const update = () => {
            if (!this._alive || !this._attachment) return GLib.SOURCE_REMOVE;
            const left = Math.max(0, Math.round((this._attachment.until - GLib.get_monotonic_time()) / 1000000));
            if (!left) {
                this._attachmentTimer = 0;
                this._setAttachment(null);
                return GLib.SOURCE_REMOVE;
            }
            this._attachmentLabel.text = `Recuerdo tu captura${this._attachment.app ? ' de ' + this._attachment.app : ''} ` +
                `(${where[this._attachment.scope] ?? 'captura'}) · solo en memoria · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
            return GLib.SOURCE_CONTINUE;
        };
        update();
        this._attachmentTimer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, update);
    }

    // Leo atento: his posture says what he watches — walking on the desktop, everything; inside a window,
    // only that window; still or asleep, nothing.
    _watchMode() {
        if (!this._settings.watch || !this._pet?.visible) return 'off';
        if (this._perch) return 'perched';
        const motion = this._settings.motion && this._interface.get_boolean('enable-animations');
        return this._settings.roam && motion ? 'all' : 'off';
    }

    _syncWatch(force = false) {
        if (!this._alive) return;
        const mode = this._watchMode();
        this._syncWindow(mode);
        if (mode === this._watchSent && !force) return;
        if (mode === 'off' && this._watchSent === null && !force) return;
        this._watchSent = mode;
        this._call({op: 'watch', action: 'state', args: {mode}}, response => {
            if (!response.ok && this._watchSent === mode) this._watchSent = null;  // Retried on the next second.
        }, {timeout: 5000});
    }

    // F6.4: the title of the window Leo watches tells the daemon the project; the card stays discreet (📁).
    _syncWindow(mode) {
        if (mode === 'off') {
            this._titleSent = null;
            if (this._context) this._setContext(null);
            return;
        }
        let window = this._perch?.window ?? global.display.focus_window;
        if (window?.get_gtk_application_id() === 'io.github.leo.Companion.UI') window = null;
        const title = (window?.get_title() ?? '').slice(0, 300);
        // The app and its ids let the daemon keep protected windows (password managers…) out of the diary.
        const app = window ? Shell.WindowTracker.get_default().get_window_app(window)?.get_name()?.slice(0, 120) : null;
        const key = `${title}\n${app ?? ''}`;
        if (key === this._titleSent || this._watchSent !== mode) return;
        this._titleSent = key;
        const args = {title, ids: window ? identities(window).map(id => id.slice(0, 200)).slice(0, 6) : []};
        if (app) args.app = app;
        this._call({op: 'watch', action: 'window', args}, response => {
            if (!response.ok) {
                if (this._titleSent === key) this._titleSent = null;  // Retried on the next second.
                return;
            }
            this._setContext(response.context ?? null);
        }, {timeout: 10000});
    }

    _loadSeen() {
        this._seenLoaded = false; // Never act on the list of a previous opening while the new one arrives.
        this._seenBox.visible = false;
        this._call({op: 'watch', action: 'seen', args: {limit: 40}}, response => {
            if (!this._seenBox) return;
            this._seen = response.ok && Array.isArray(response.entries) ? response.entries.slice().reverse() : [];
            this._seenLoaded = true;
            if (this._seenFocus && !this._seen.some(item => item.id === this._seenFocus)) this._chooseSeen(null);
            this._renderSeen();
        }, {timeout: 5000});
    }

    _seenLabel(item) {
        const at = String(item.at ?? '').slice(11, 16), until = String(item.until ?? '').slice(11, 16);
        const what = item.document?.name ?? item.title ?? item.project ?? (item.scope === 'all' ? 'escritorio' : 'ventana');
        const text = `${at}${until && until !== at ? '–' + until : ''} · ${item.app ? item.app + ' · ' : ''}${what}`;
        return (item.finding ? '⚠ ' : '') + (text.length > 52 ? text.slice(0, 51) + '…' : text);
    }

    _renderSeen() {
        this._seenList.destroy_all_children();
        const items = this._seen ?? [];
        this._seenBox.visible = items.length > 0;
        if (!items.length) return;
        const shown = this._seenExpanded ? items : items.slice(0, 3);
        this._seenTitle.text = `👁 lo que vi hoy · ${items.length}`;
        this._seenMore.visible = items.length > 3;
        this._seenMore.label = this._seenExpanded ? 'menos' : 'más';
        this._seenScroll.vscrollbar_policy = this._seenExpanded ? St.PolicyType.AUTOMATIC : St.PolicyType.NEVER;
        const theme = this._theme;
        for (const item of shown) {
            const chosen = item.id === this._seenFocus;
            const row = new St.Button({style_class: 'leo-seen-item', can_focus: true, x_expand: true,
                accessible_name: `Preguntar a Leo sobre lo que vio a las ${String(item.at ?? '').slice(11, 16)}`});
            const box = new St.BoxLayout({vertical: true, x_expand: true});
            const label = new St.Label({text: this._seenLabel(item), style_class: 'leo-seen-text', x_expand: true});
            box.add_child(label);
            if (chosen) {
                const detail = item.note || (item.lines ?? []).slice(0, 2).join(' / ') || item.title || '';
                if (detail) {
                    const more = new St.Label({text: detail.slice(0, 220), style_class: 'leo-seen-detail', x_expand: true});
                    more.clutter_text.set_line_wrap(true);
                    more.clutter_text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR);
                    box.add_child(more);
                }
            }
            row.set_child(box);
            if (theme)
                row.set_style(`background-color: ${chosen ? theme.header_bg : 'transparent'}; color: ${item.finding ? theme.palette[11] : theme.fg};` +
                    ` border: 1px solid ${chosen ? theme.accent : 'transparent'}; border-radius: 4px;`);
            row.connect('clicked', () => this._chooseSeen(chosen ? null : item.id));
            this._seenList.add_child(row);
        }
    }

    _chooseSeen(id) {
        this._seenFocus = id;
        const item = id ? this._seen?.find(entry => entry.id === id) : null;
        if (this._entry) {
            this._entry.hint_text = item ? `pregunta sobre lo de las ${String(item.at).slice(11, 16)}…` : 'tú@local:~$ escribe a Leo…';
            if (item) this._entry.grab_key_focus();
        }
        if (this._seenList) this._renderSeen();
    }

    _setContext(card) {
        if ((card?.path ?? null) !== (this._context?.path ?? null)) this._contextSeen = !card;
        this._context = card;
        if (!this._contextLabel) return;
        this._contextLabel.visible = !!card;
        if (card) {
            const changes = card.changes === 1 ? '1 cambio' : `${card.changes} cambios`;
            const details = [card.branch ? `rama ${card.branch}` : '', card.changes !== null && card.changes !== undefined
                ? changes : ''].filter(Boolean).join(' · ');
            this._contextLabel.text = `📁 ${card.name}${details ? ' · ' + details : ''}${card.next ? '\n' + card.next : ''}`;
        }
    }

    _placeDecorations(now) {
        const visible = this._pet.visible && !this._capturing;
        const motion = this._settings.motion && this._interface.get_boolean('enable-animations');
        this._placeThirdEye(now, visible, motion);
        this._badge.visible = visible && !!this._context && !this._contextSeen;
        if (this._badge.visible) this._badge.set_position(Math.round(this._pet.x + SIZE * .14), Math.round(this._pet.y + 4));
        if (!this._agentPets.size) return;
        const areas = this._areas();
        const area = areas.length ? areas[nearestMonitor(this._pet, areas)] : null;
        const step = AGENT_SIZE + 3, count = this._agentPets.size;
        const right = !area || this._pet.x + SIZE + count * step < area.x + area.width;
        let i = 0;
        for (const item of this._agentPets.values()) {
            const x = right ? this._pet.x + SIZE - 10 + i * step : this._pet.x + 10 - (i + 1) * step;
            const bob = motion ? Math.abs(Math.sin(now / 280000 + i * 1.3)) * 3 : 0;
            item.actor.visible = visible && !item.leaving;
            item.actor.set_position(Math.round(x), Math.round(this._pet.y + FEET - AGENT_SIZE - bob));
            if (!item.leaving) item.actor.rotation_angle_z = motion ? Math.sin(now / 350000 + i * 2.1) * 7 : 0; // Busy cooks wiggle.
            i++;
        }
    }

    _loadThirdEye() {
        try {
            const [, bytes] = Gio.File.new_for_path(`${this.path}/assets/third-eye.json`).load_contents(null);
            const anchors = JSON.parse(new TextDecoder().decode(bytes));
            return anchors && typeof anchors === 'object' ? anchors : null;
        } catch (_error) {
            return null; // Without anchors the eye floats beside Leo's head, where the old 👁 was.
        }
    }

    // The third eye opens on the forehead of the current pose or walking frame (mirrored with the walk) and blinks
    // now and then, like his other eyes; with reduced motion it just appears, still.
    _placeThirdEye(now, visible, motion) {
        const watching = visible && !!this._watchSent && this._watchSent !== 'off';
        this._eyeOpen = motion ? clamp(this._eyeOpen + (watching ? .12 : -.2), 0, 1) : watching ? 1 : 0;
        const shown = visible && this._eyeOpen > .01;
        this._eye.visible = shown;
        if (!shown) return;
        const name = this._lastFrame ?? 'sit';
        let anchor = this._thirdEye?.[name];
        if (anchor && this._sprites[name]?.get_path()?.endsWith('.png')) anchor = null; // Photo sprites: other geometry.
        const k = SIZE / CANVAS, mirrored = name.startsWith('walk') && this._side < 0;
        const x = this._pet.x + this._pet.translation_x + (anchor ? (mirrored ? CANVAS - anchor.x : anchor.x) * k : SIZE * .78);
        const y = this._pet.y + this._pet.translation_y + (anchor ? anchor.y * k : SIZE * .1);
        const seconds = now / 1000000, phase = seconds % 6.5;
        const blink = motion && phase < .18 ? Math.max(.08, Math.abs(phase - .09) / .09) : 1;
        this._eye.set_position(Math.round(x - THIRD_EYE / 2), Math.round(y - THIRD_EYE / 2));
        this._eye.rotation_angle_z = anchor ? (mirrored ? -anchor.angle : anchor.angle) : 0;
        this._eye.scale_x = anchor?.profile ? .72 : 1; // Seen from the side while walking.
        this._eye.scale_y = this._eyeOpen * blink;
        const parent = this._pet.get_parent();
        if (parent && this._eye.get_parent() === parent && now - this._eyeStacked > 1000000) {
            this._eyeStacked = now; // Just above Leo (anything already above him, like the balloon, stays above).
            parent.set_child_above_sibling(this._eye, this._pet);
        }
    }

    _agentIcon(tool) {
        // The user may drop an official logo here; otherwise Leo's own original mascots.
        for (const name of [`${tool}.png`, `${tool}.svg`]) {
            const custom = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_data_dir(), 'leo', 'agents', name]));
            if (custom.query_exists(null)) return new Gio.FileIcon({file: custom});
        }
        return new Gio.FileIcon({file: Gio.File.new_for_path(`${this.path}/agent-${tool}.svg`)});
    }

    _updateAgentPets(list) {
        const wanted = new Map(list.filter(pet => AGENT_TOOLS.has(pet?.tool) && typeof pet.id === 'string')
            .slice(0, 12).map(pet => [pet.id, pet]));
        for (const [id, item] of this._agentPets) {
            if (wanted.has(id) || item.leaving) continue;
            item.leaving = true;
            item.actor.ease({opacity: 0, scale_x: .3, scale_y: .3, duration: 300, mode: Clutter.AnimationMode.EASE_IN_QUAD,
                onComplete: () => this._dropAgentPet(id)});
        }
        for (const [id, pet] of wanted) {
            if (this._agentPets.has(id)) continue;
            const project = typeof pet.project === 'string' && pet.project ? ` en ${pet.project.slice(0, 60)}` : '';
            const actor = new St.Icon({gicon: this._agentIcon(pet.tool), icon_size: AGENT_SIZE, reactive: false,
                accessible_name: `${pet.label || pet.tool} está trabajando${project}`, visible: false});
            Main.layoutManager.addChrome(actor, {affectsStruts: false, trackFullscreen: false});
            actor.set_pivot_point(.5, 1);
            actor.scale_x = actor.scale_y = .2;
            actor.ease({scale_x: 1, scale_y: 1, duration: 260, mode: Clutter.AnimationMode.EASE_OUT_BACK});
            this._agentPets.set(id, {actor, tool: pet.tool, leaving: false});
        }
    }

    _dropAgentPet(id) {
        const item = this._agentPets.get(id);
        if (!item) return;
        this._agentPets.delete(id);
        Main.layoutManager.removeChrome(item.actor);
        item.actor.destroy();
    }

    _decorations() {
        return [this._eye, this._badge, ...[...this._agentPets.values()].map(item => item.actor)];
    }

    _forget() {
        if (!this._attachment) return;
        this._setAttachment(null);
        this._call({op: 'look', action: 'forget', args: {}}, () => {}, {timeout: 5000});
    }

    _position() {
        const areas = this._areas();
        if (!areas.length) return;
        const saved = this._settings.position;
        const index = saved && saved.monitor < areas.length ? saved.monitor : Main.layoutManager.primaryIndex;
        const area = areas[Math.max(0, index)];
        const point = saved ? {x: area.x + saved.x * Math.max(0, area.width - SIZE),
            y: area.y + saved.y * Math.max(0, area.height - SIZE)} :
            {x: this._settings.corner === 'left' ? area.x + 16 : area.x + area.width - SIZE - 16,
                y: area.y + area.height - SIZE - 12};
        const safe = contain(point, area);
        this._pet.set_position(safe.x, safe.y);
        this._route = null;
        this._visibility();
    }

    _layoutChanged() {
        if (this._drag) this._finishDrag(true);
        this._route = null;
        const areas = this._areas();
        if (areas.length) {
            const p = contain(this._pet, areas[nearestMonitor(this._pet, areas)]);
            this._pet.set_position(p.x, p.y);
        }
        this._visibility();
    }

    _fitBubble() {
        const areas = this._areas();
        if (!areas.length) return;
        const area = areas[nearestMonitor(this._pet, areas)];
        this._bubble.actor.set_style(`max-width: ${Math.max(200, Math.min(390, area.width - 24))}px;`);
        // A pet perched high on a window needs its balloon below its feet.
        this._bubble._boxPointer.updateArrowSide(this._pet.y - area.y < 300 ? St.Side.TOP : St.Side.BOTTOM);
    }

    _visibility() {
        if (this._capturing) return;
        if (!this._alive || !this._pet) return;
        const areas = this._areas();
        const index = areas.length ? nearestMonitor(this._pet, areas) : -1;
        const perchVisible = !this._perch || (!this._perch.window.minimized &&
            this._perch.window.located_on_workspace(global.workspace_manager.get_active_workspace()));
        this._pet.visible = index >= 0 && this._settings.visible && !this._settings.focus &&
            !Main.overview.visible && !Main.sessionMode.isLocked && perchVisible &&
            !global.display.get_monitor_in_fullscreen(index);
        if (!this._pet.visible) {
            this._bubble?.close(0);
            if (this._drag) this._finishDrag(true);
        }
    }

    _setupDrag() {
        // Use GNOME's native threshold gesture: a click still opens chat, a drag
        // claims the sequence and cannot accidentally activate the button.
        this._dragGesture = new St.DndStartGesture();
        this._dragGesture.connect('recognize', () => {
            const event = this._dragGesture.get_point_begin_event();
            this._beginDrag(...event.get_coords());
        });
        this._pet.add_action(this._dragGesture);
        this._dragActor = new Clutter.Actor({reactive: true, width: 0, height: 0});
        Main.uiGroup.add_child(this._dragActor);
        this._dragActor.connect('event', (_actor, event) => {
            if (!this._drag) return Clutter.EVENT_PROPAGATE;
            if (event.type() === Clutter.EventType.MOTION || event.type() === Clutter.EventType.TOUCH_UPDATE)
                this._dragTo(...event.get_coords());
            else if (event.type() === Clutter.EventType.BUTTON_RELEASE || event.type() === Clutter.EventType.TOUCH_END)
                this._finishDrag(false);
            else if (event.type() === Clutter.EventType.TOUCH_CANCEL ||
                (event.type() === Clutter.EventType.KEY_PRESS && event.get_key_symbol() === Clutter.KEY_Escape))
                this._finishDrag(true);
            return Clutter.EVENT_PROPAGATE;
        });
    }

    _beginDrag(x, y) {
        this._bubble.close(0);
        this._route = null;
        this._errand = null;
        this._drag = {x: this._pet.x, y: this._pet.y, dx: x - this._pet.x, dy: y - this._pet.y};
        this._dragGrab = Main.pushModal(this._dragActor);
        this._dragActor.set_cursor_type(Clutter.CursorType.MOVE);
        this._frame('happy');
    }

    _dragTo(x, y) {
        if (!this._drag) return;
        const point = {x: x - this._drag.dx, y: y - this._drag.dy};
        // Continuous during cross-monitor dragging; final release is clamped.
        this._pet.set_position(point.x, point.y);
    }

    _finishDrag(cancelled) {
        if (!this._drag) return;
        const original = this._drag;
        this._drag = null;
        if (this._dragGrab) Main.popModal(this._dragGrab);
        this._dragGrab = null;
        if (cancelled) {
            this._pet.set_position(original.x, original.y);
        } else {
            this._detach();
            const areas = this._areas();
            if (areas.length) {
                const safe = contain(this._pet, areas[nearestMonitor(this._pet, areas)]);
                this._pet.set_position(safe.x, safe.y);
                this._tryPerch();
                this._pin();
            }
        }
        this._tick();
    }

    _tryPerch() {
        // Dropped on a window (its top edge or inside it), Leo lives there: he walks slowly within its
        // borders, follows it, watches only it, and leaves only when the user drags him out.
        const workspace = global.workspace_manager.get_active_workspace();
        const windows = global.display.sort_windows_by_stacking(
            global.get_window_actors().map(actor => actor.meta_window)).reverse();
        for (const window of windows) {
            if (window.minimized || window.is_fullscreen() || !window.located_on_workspace(workspace)) continue;
            if (window.get_gtk_application_id() === 'io.github.leo.Companion.UI') continue;
            const rect = window.get_frame_rect();
            const center = this._pet.x + SIZE / 2, feet = this._pet.y + FEET;
            if (center < rect.x + 24 || center > rect.x + rect.width - 24 ||
                feet < rect.y - 32 || feet > rect.y + rect.height + 8) continue;
            const inside = feet > rect.y + 32 && rect.height >= SIZE + 16 && rect.width >= SIZE + 32;
            const area = this._areas()[window.get_monitor()];
            if (!inside && (!area || rect.y - FEET < area.y || Math.abs(feet - rect.y) > 32)) continue;
            this._perch = {window, inside, fraction: clamp((center - rect.x) / rect.width, 0, 1),
                offset: inside ? clamp(this._pet.y - rect.y, 0, rect.height - SIZE) : -FEET,
                walk: true, target: null, restUntil: GLib.get_monotonic_time() + 3000000, signals: []};
            for (const signal of ['position-changed', 'size-changed', 'notify::minimized', 'workspace-changed'])
                this._perch.signals.push(window.connect(signal, () => this._followPerch()));
            this._perch.signals.push(window.connect('unmanaged', () => { this._detach(); this._layoutChanged(); }));
            this._followPerch();
            break;
        }
        this._syncWatch(true);
    }

    _perchBounds() {
        const rect = this._perch.window.get_frame_rect();
        const low = rect.x, high = rect.x + Math.max(0, rect.width - SIZE);
        const y = this._perch.inside ? clamp(rect.y + this._perch.offset, rect.y, rect.y + Math.max(0, rect.height - SIZE))
            : rect.y - FEET;
        return {rect, low, high, y};
    }

    _followPerch() {
        if (!this._alive || !this._perch || this._drag) return;
        const {rect, low, high, y} = this._perchBounds();
        const areas = this._areas();
        if (!areas.length) return;
        const point = {x: clamp(rect.x + rect.width * this._perch.fraction - SIZE / 2, low, high), y};
        const safe = this._perch.inside ? point : contain(point, areas[nearestMonitor(point, areas)]);
        this._pet.set_position(safe.x, safe.y);
        this._visibility();
    }

    _walkInPerch(now, dt) {
        // A slow stroll along the window, never past its borders; the window may move meanwhile.
        const {rect, low, high, y} = this._perchBounds();
        if (this._perch.target === null) this._perch.target = low + Math.random() * (high - low);
        const pace = PERCH_PACE[this._settings.pace] ?? PERCH_PACE.normal;
        const arrived = this._step({x: clamp(this._perch.target, low, high), y}, pace * dt);
        this._perch.fraction = clamp((this._pet.x + SIZE / 2 - rect.x) / Math.max(1, rect.width), 0, 1);
        if (arrived) {
            this._perch.target = null;
            this._perch.restUntil = now + (6 + Math.random() * 10) * 1000000;
            this._phase = Math.random() > .6 ? 60 : 0;
        }
    }

    _detach() {
        if (!this._perch) return;
        for (const signal of this._perch.signals) this._perch.window.disconnect(signal);
        this._perch = null;
        this._syncWatch(true);
    }

    _pin() {
        const areas = this._areas();
        if (!areas.length) return;
        const monitor = nearestMonitor(this._pet, areas), area = areas[monitor];
        this._set({roam: false, position: {monitor,
            x: clamp((this._pet.x - area.x) / Math.max(1, area.width - SIZE), 0, 1),
            y: clamp((this._pet.y - area.y) / Math.max(1, area.height - SIZE), 0, 1)}});
        this._route = null;
        this._phase = 0;
    }

    _chooseWalk() {
        const areas = this._areas();
        if (!areas.length) return;
        const from = nearestMonitor(this._pet, areas);
        const candidates = this._settings.roam_scope === 'current' ? [from] : areas.map((_, i) => i);
        // Shuffle destinations; every reachable monitor may be explored.
        for (let i = candidates.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [candidates[i], candidates[j]] = [candidates[j], candidates[i]];
        }
        for (const to of candidates) {
            if (global.display.get_monitor_in_fullscreen(to)) continue;
            const area = areas[to];
            const destination = {x: area.x + Math.random() * Math.max(0, area.width - SIZE),
                y: area.y + Math.random() * Math.max(0, area.height - SIZE)};
            const path = route(areas, from, to, destination);
            // Reject paths through any fullscreen monitor.
            if (path && path.every(p => !global.display.get_monitor_in_fullscreen(nearestMonitor(p, areas)))) {
                this._route = path;
                return;
            }
        }
    }

    _petLeo() {
        if (!this._pet.visible || this._drag || this._busy) return;
        const now = GLib.get_monotonic_time();
        this._affectionStart = now;
        this._affectionUntil = now + 3500000;
        this._restUntil = this._affectionUntil + 2000000;
        this._phase = 0;
        this._tick();
    }

    _pointerReaction(now) {
        if (!this._pet.hover || this._bubble.isOpen || this._busy) return;
        // Sample only while pointing at Leo; no global pointer history is retained.
        const [x, y] = global.get_pointer();
        const previous = this._pointerSample;
        if (previous && now - previous.at < 150000) {
            const distance = Math.hypot(x - previous.x, y - previous.y);
            const onHead = y - this._pet.y < SIZE * .70 && y - this._pet.y > SIZE * .16;
            this._touchDistance = onHead ? this._touchDistance * .96 + Math.min(distance, 24) : 0;
            if (this._touchDistance > 65 && now > this._affectionUntil) {
                this._touchDistance = 0;
                this._petLeo();
            }
        }
        this._pointerSample = {x, y, at: now};
    }

    _animate() {
        if (this._capturing) return;
        const now = GLib.get_monotonic_time();
        const dt = Math.min(.1, (now - this._lastTime) / 1000000);
        this._lastTime = now;
        if (this._toast.visible && (now >= this._toastUntil || !this._pet.visible || this._bubble.isOpen))
            this._toast.hide();
        else
            this._placeToast(); // Follows Leo while he walks or rides a moving window.
        this._placeDecorations(now);
        if (now - this._watchCheck > 1000000) {
            this._watchCheck = now;
            this._syncWatch();
        }
        if (this._drag || !this._pet.visible) return;
        const motion = this._settings.motion && this._interface.get_boolean('enable-animations');
        if (this._errand && (!motion || this._bubble.isOpen)) this._errand = null;
        if (this._errand && !this._pet.hover) {
            if (this._step(this._errand[0], 90 * dt)) this._errand.shift();
            if (!this._errand.length) {
                const pointing = this._errandKind === 'point';
                this._errand = null;
                this._errandKind = '';
                // Rest on the window's top edge; while roaming, just pause there for a while.
                if (!this._settings.roam && !pointing) this._tryPerch();
                this._restUntil = now + 20000000;
                this._tick();
            }
            this._visibility();
            return;
        }
        this._pointerReaction(now);
        if (now < this._affectionUntil && !this._busy) {
            this._frame(motion ? `pet-${Math.floor((now - this._affectionStart) / 110000) % 8}` : 'happy');
            return;
        }
        if (this._perch?.walk && motion && !this._pet.hover && !this._bubble.isOpen && !this._busy &&
            now >= this._perch.restUntil) {
            this._walkInPerch(now, dt);
            this._visibility();
            return;
        }
        const walking = motion && this._settings.roam && !this._pet.hover && !this._bubble.isOpen && !this._busy;
        if (walking && now >= this._restUntil) {
            if (!this._route?.length) this._chooseWalk();
            if (this._route?.length) {
                const pace = {slow: 32, normal: 54, lively: 78}[this._settings.pace] ?? 54;
                if (this._step(this._route[0], pace * dt)) {
                    this._route.shift();
                    if (!this._route.length) {
                        this._restUntil = now + (4 + Math.random() * 8) * 1000000;
                        this._phase = Math.random() > .65 ? 60 : 0;
                    }
                }
                this._visibility();
                return;
            }
        }
        this._idleElapsed += dt;
        if (this._idleElapsed >= .8 || this._lastFrame.startsWith('walk')) {
            this._idleElapsed = 0;
            this._tick();
        }
    }

    _tick() {
        if (!this._pet.visible || this._drag) return;
        const moving = this._settings.motion && this._interface.get_boolean('enable-animations') &&
            !this._pet.hover && !this._bubble.isOpen && !this._busy;
        if (moving) this._phase = (this._phase + 1) % 100;
        let pose = 'sit';
        if (GLib.get_monotonic_time() < this._pointUntil) pose = this._pointPose;
        else if (this._busy && this._reading) pose = 'reading';
        else if (this._busy || this._state === 'thinking') pose = 'alert';
        else if (GLib.get_monotonic_time() < this._alertUntil) pose = this._alertPose;
        else if (GLib.get_monotonic_time() < this._affectionUntil) pose = 'happy';
        else if (this._bubble.isOpen) pose = 'happy';
        else if (this._pet.hover) pose = global.get_pointer()[0] < this._pet.x + SIZE / 2 ? 'curious-left' : 'curious-right';
        else if (this._jobs > 0) pose = 'watching'; // Keeps an eye on watched jobs instead of dozing.
        else if (this._phase >= 55 && this._phase < 90) pose = 'sleep';
        else if (moving && this._phase % 17 === 16) pose = 'blink';
        this._frame(pose);
    }

    _frame(name) {
        this._image.scale_x = name.startsWith('walk') ? this._side : 1;
        if (name === this._lastFrame) return;
        this._image.gicon = new Gio.FileIcon({file: this._spriteFile(name)});
        this._lastFrame = name;
    }

    _spriteFile(name) {
        // PNG sprites made locally from the user's photos (installed only on this computer, never packaged)
        // win over the illustrated SVG, which remains the fallback for any missing pose or frame.
        if (!(name in this._sprites)) {
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            const files = (scale > 1 ? [`${name}@2x.png`, `${name}.png`] : [`${name}.png`])
                .map(file => Gio.File.new_for_path(`${this.path}/assets/generated/${file}`));
            this._sprites[name] = files.find(file => file.query_exists(null)) ??
                Gio.File.new_for_path(`${this.path}/assets/${name}.svg`);
        }
        return this._sprites[name];
    }

    disable() {
        this._alive = false;
        this._ungrabShortcut();
        for (const source of [this._attachmentTimer, this._regionTimer])
            if (source) GLib.Source.remove(source);
        this._attachmentTimer = this._regionTimer = 0;
        this._attachment = null;
        for (const id of [...(this._agentPets?.keys() ?? [])]) this._dropAgentPet(id);
        for (const actor of [this._eye, this._badge]) {
            if (!actor) continue;
            Main.layoutManager.removeChrome(actor);
            actor.destroy();
        }
        this._eye = this._badge = this._contextLabel = null;
        this._context = null;
        this._watchItem = this._grayItem = null;
        this._captureService?.destroy();
        this._captureService = null;
        this._cancellable?.cancel();
        if (this._drag) this._finishDrag(true);
        this._detach();
        this._dragActor?.destroy();
        this._dragActor = this._dragGesture = null;
        this._route = null;
        for (const source of [this._animation, this._poller])
            if (source) GLib.Source.remove(source);
        for (const [object, signal] of this._signals ?? []) object.disconnect(signal);
        this._signals = [];
        for (const id of [this._chunkSignal, this._eventSignal, this._planSignal, this._pointSignal])
            if (id) Gio.DBus.session.signal_unsubscribe(id);
        this._chunkSignal = this._eventSignal = this._planSignal = this._pointSignal = 0;
        this._pointTarget = null;
        this._sprites = {};
        this._requestId = this._approval = null;
        this._streamId = null;
        this._errand = null;
        this._queued = [];
        if (this._toast) {
            Main.layoutManager.removeChrome(this._toast);
            this._toast.destroy();
            this._toast = null;
        }
        if (this._bubble) {
            this._bubble.close(0);
            this._bubbleManager.removeMenu(this._bubble);
            Main.layoutManager.removeChrome(this._bubble.actor);
            this._bubble.destroy();
        }
        if (this._pet) {
            this._pet.remove_all_transitions();
            Main.layoutManager.removeChrome(this._pet);
            this._pet.destroy();
        }
        this._indicator?.destroy();
        this._pet = this._image = this._indicator = this._interface = null;
        this._focusItem = this._motionItem = this._visibleItem = this._roamItem = null;
        this._cancellable = null;
        this._bubble = this._bubbleManager = this._entry = this._reply = this._scroll = null;
        this._sendButton = this._status = null;
        this._localOverrides = {};
        this._animation = this._poller = 0;
    }
}
