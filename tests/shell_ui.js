// Panel GTK en GNOME/Wayland real, exclusivamente a través de scripts/test_shell.py --ui-only.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};
export async function run() {
    await Scripting.sleep(1800);
    Main.overview.hide();
    const extension = Main.extensionManager.lookup('leo@local.desktop');
    if (extension?.state !== 1)
        throw new Error('Extension not enabled: ' + extension?.error);
    const icon = extension.stateObj._indicator.get_first_child();
    if (!icon.gicon || icon.gicon.get_file().get_basename() !== 'sit.svg')
        throw new Error('Panel must show the same illustrated Leo as settings');
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE});
    launcher.setenv('LEO_UI_PREVIEW_PATH', '', true);
    const ui = launcher.spawnv(['dbus-run-session', '--', '/usr/bin/python3',
        GLib.getenv('LEO_PROJECT_ROOT') + '/tests/smoke_ui.py']);
    const [stdout, stderr] = await new Promise((resolve, reject) => {
        ui.communicate_utf8_async(null, null, (process, result) => {
            try {
                const [, output, errors] = process.communicate_utf8_finish(result);
                resolve([output, errors]);
            } catch (error) { reject(error); }
        });
    });
    if (!ui.get_successful() || !stdout.includes('LEO_GTK_PASS') || !stdout.includes('LEO_GTK_CLOSE_PASS'))
        throw new Error('GTK failed: ' + stdout + '\n' + stderr);
    print(stdout);
    print('LEO_SHELL_PASS: extension enabled and GTK settings with Cerveau controls; isolated Wayland');
}
