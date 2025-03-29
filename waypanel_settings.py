import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import toml
import webview
from hypercorn.asyncio import serve
from hypercorn.config import Config
from quart import Quart, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

app = Quart(__name__)
app.secret_key = 'your-secret-key-here'
app.config['CONFIG_PATH'] = os.path.expanduser('~/.config/waypanel/waypanel.toml')

__all__ = ['Config', 'load_config', 'save_config']


def clean_orphaned_sections(config):
    """
    Specifically removes empty sections that:
    1. Are in the format [prefix.suffix]
    2. Have no contents
    3. Aren't standard configuration sections
    """
    # List of protected section names that should never be removed
    PROTECTED_SECTIONS = {
        'panel', 'menu', 'folders', 'dockbar',
        'cmd', 'launcher', 'dpms'  # Add all your real sections here
    }

    # Create a list of all section names in the config
    all_sections = list(config.keys())

    for section_name in all_sections:
        # Check if this is a dotted section (like test.something)
        if '.' in section_name:
            prefix = section_name.split('.')[0]

            # Only remove if:
            # 1. The prefix isn't a protected section
            # 2. The section is completely empty
            if (prefix not in PROTECTED_SECTIONS and
                    not config[section_name]):
                del config[section_name]
                print(f"Removed empty orphaned section: [{section_name}]")

    return config


def load_config():
    try:
        with open(app.config['CONFIG_PATH'], 'r') as f:
            config = toml.load(f)
            return clean_orphaned_sections(config)
    except FileNotFoundError:
        return {
            'panel': {},
            'menu': {'icons': {}},
            'folders': {},
            'dockbar': {},
            'cmd': {},
            'launcher': {}
        }


@app.route('/reload_waypanel', methods=['POST'])
async def reload_waypanel():
    try:
        # Kill and restart waypanel
        subprocess.Popen('pkill waypanel; waypanel', shell=True)
        return {'success': True, 'message': 'Waypanel reload initiated'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def save_config(config):
    """Save config with proper TOML formatting"""
    # First clean empty sections
    if 'menu' in config:
        config['menu'] = {k: v for k, v in config['menu'].items() if v}

    # Write to temporary file first for atomic save
    temp_path = f"{app.config['CONFIG_PATH']}.tmp"
    with open(temp_path, 'w') as f:
        toml.dump(config, f)

    # Replace original file
    os.replace(temp_path, app.config['CONFIG_PATH'])


def get_menu_items(submenu_data):
    """Extract menu items from submenu data in any format"""
    items = []

    if isinstance(submenu_data, list):
        # Handle array-of-tables format: [[menu.VPN.item1], [menu.VPN.item2]]
        items = submenu_data
    elif isinstance(submenu_data, dict):
        # Handle dictionary format: [menu.VPN.item1], [menu.VPN.item2]
        items = [value for key, value in submenu_data.items()
                 if key.startswith('item') and isinstance(value, dict)]

    # Add position information for sorting
    for idx, item in enumerate(items):
        if '_key' not in item:
            item['_key'] = f'item_{idx + 1}'

    # Sort items by their key (item1, item2, etc.)
    items.sort(key=lambda x: int(x['_key'].split('_')[-1]))

    return items

# ======================
# Dockbar Routes
# ======================


@app.route('/dockbar')
async def dockbar():
    config = load_config()
    return await render_template('dockbar.html', apps=config.get('dockbar', {}))


@app.route('/dockbar/add', methods=['GET', 'POST'])
async def add_dockbar_app():
    if request.method == 'POST':
        form = await request.form
        config = load_config()

        app_id = secure_filename(form['id'])
        config.setdefault('dockbar', {})[app_id] = {
            'cmd': form['cmd'],
            'icon': form['icon'],
            'wclass': form.get('wclass', ''),
            'desktop_file': form.get('desktop_file', ''),
            'name': form['name'],
            **({'initial_title': form['initial_title']} if 'initial_title' in form else {})
        }

        save_config(config)
        return redirect(url_for('dockbar'))

    return await render_template('add_dockbar_app.html')


@app.route('/dockbar/edit/<app_id>', methods=['GET', 'POST'])
async def edit_dockbar_app(app_id):
    config = load_config()

    if request.method == 'POST':
        form = await request.form
        config['dockbar'][app_id] = {
            'cmd': form['cmd'],
            'icon': form['icon'],
            'wclass': form.get('wclass', ''),
            'desktop_file': form.get('desktop_file', ''),
            'name': form['name'],
            **({'initial_title': form['initial_title']} if 'initial_title' in form else {})
        }
        save_config(config)
        return redirect(url_for('dockbar'))

    app_config = config.get('dockbar', {}).get(app_id, {})
    return await render_template('edit_dockbar_app.html', app_id=app_id, app=app_config)


@app.route('/dockbar/delete/<app_id>', methods=['POST'])
async def delete_dockbar_app(app_id):
    config = load_config()
    if 'dockbar' in config and app_id in config['dockbar']:
        del config['dockbar'][app_id]
        save_config(config)
    return redirect(url_for('dockbar'))


@app.route('/dockbar/move_up/<app_name>', methods=['POST'])
async def move_dockbar_app_up(app_name):
    try:
        config = load_config()
        print(f"\n=== MOVE UP DEBUG START ===")
        print(f"Attempting to move UP: {app_name}")

        # Get the dockbar dictionary
        dockbar_items = config.get('dockbar', {})
        print(f"Dockbar items: {list(dockbar_items.keys())}")

        if app_name not in dockbar_items:
            print(f"ERROR: {app_name} not found in dockbar items")
            await flash('Application not found', 'error')
            return redirect(url_for('dockbar'))

        # Convert to list to maintain order
        items_list = list(dockbar_items.items())
        current_index = next(i for i, (k, v) in enumerate(items_list) if k == app_name)

        print(f"Current position: {current_index}")

        if current_index <= 0:
            print("Already at top position")
            await flash('Already at top', 'info')
            return redirect(url_for('dockbar'))

        # Swap positions
        prev_index = current_index - 1
        items_list[current_index], items_list[prev_index] = items_list[prev_index], items_list[current_index]

        # Update the config
        config['dockbar'] = dict(items_list)

        print("New order:")
        for name in config['dockbar'].keys():
            print(f"- {name}")

        save_config(config)
        print("=== MOVE UP DEBUG END ===\n")
        await flash(f'Moved {app_name} up', 'success')

    except Exception as e:
        print(f"ERROR: {str(e)}")
        await flash('Error moving application', 'error')

    return redirect(url_for('dockbar'))


@app.route('/dockbar/move_down/<app_name>', methods=['POST'])
async def move_dockbar_app_down(app_name):
    try:
        config = load_config()
        print(f"\n=== DEBUG START ===")
        print(f"Attempting to move DOWN: {app_name}")

        # Get the dockbar dictionary
        dockbar_items = config.get('dockbar', {})
        print(f"Dockbar items: {list(dockbar_items.keys())}")

        if app_name not in dockbar_items:
            print(f"ERROR: {app_name} not found in dockbar items")
            await flash('Application not found', 'error')
            return redirect(url_for('dockbar'))

        # Convert to list to maintain order
        items_list = list(dockbar_items.items())
        current_index = next(i for i, (k, v) in enumerate(items_list) if k == app_name)

        print(f"Current position: {current_index}")

        if current_index >= len(items_list) - 1:
            print("Already at bottom position")
            await flash('Already at bottom', 'info')
            return redirect(url_for('dockbar'))

        # Swap positions
        next_index = current_index + 1
        items_list[current_index], items_list[next_index] = items_list[next_index], items_list[current_index]

        # Update the config
        config['dockbar'] = dict(items_list)

        print("New order:")
        for name in config['dockbar'].keys():
            print(f"- {name}")

        save_config(config)
        print("=== DEBUG END ===\n")
        await flash(f'Moved {app_name} down', 'success')

    except Exception as e:
        print(f"ERROR: {str(e)}")
        await flash('Error moving application', 'error')

    return redirect(url_for('dockbar'))

# ======================
# Menu Routes
# ======================


@app.route('/menu')
async def menu():
    config = load_config()
    menu_config = config.get('menu', {})
    return await render_template('menu/index.html',
                                 menu_config=menu_config,
                                 get_menu_items=get_menu_items)


@app.route('/menu/icons/edit', methods=['GET', 'POST'])
async def edit_menu_icons():
    config = load_config()

    if request.method == 'POST':
        form = await request.form
        icons = {}

        i = 0
        while f'name_{i}' in form:
            name = form[f'name_{i}']
            icon = form[f'icon_{i}']
            if name and icon:
                icons[name] = icon
            i += 1

        new_name = form.get('new_name')
        new_icon = form.get('new_icon')
        if new_name and new_icon:
            icons[new_name] = new_icon

        config.setdefault('menu', {})['icons'] = icons
        save_config(config)
        await flash('Menu icons updated!', 'success')
        return redirect(url_for('menu'))

    menu_icons = config.get('menu', {}).get('icons', {})
    return await render_template('menu/icons.html', menu_icons=menu_icons)


@app.route('/menu/submenu/<submenu_name>')
async def view_submenu(submenu_name):
    config = load_config()
    submenu_data = config.get('menu', {}).get(submenu_name, {})

    # Convert items to a common format
    items = []
    if isinstance(submenu_data, list):
        # Array-of-tables format
        items = submenu_data
    elif isinstance(submenu_data, dict):
        # Dictionary format
        items = []
        for key, item in submenu_data.items():
            if key.startswith('item'):
                if isinstance(item, dict):
                    items.append({
                        'name': item.get('name'),
                        'cmd': item.get('cmd'),
                        'key': key
                    })
                # Handle case where item might be a list or other type
                # You might want to add additional handling here if needed

    return await render_template('menu/submenu.html',
                                 submenu_name=submenu_name,
                                 items=items)


@app.route('/menu/submenu/<submenu_name>/add', methods=['GET', 'POST'])
async def add_submenu_item(submenu_name):
    config = load_config()

    if request.method == 'POST':
        form = await request.form
        new_item = {'name': form['name'], 'cmd': form['cmd']}

        # Initialize menu if not exists
        if 'menu' not in config:
            config['menu'] = {}
        if submenu_name not in config['menu']:
            config['menu'][submenu_name] = {}

        # Find next available item number
        item_num = 1
        while f'item_{item_num}' in config['menu'][submenu_name]:
            item_num += 1

        # Add new item in the correct nested format
        config['menu'][submenu_name][f'item_{item_num}'] = new_item

        save_config(config)
        await flash('Item added successfully!', 'success')
        return redirect(url_for('view_submenu', submenu_name=submenu_name))

    return await render_template('menu/add_item.html', submenu_name=submenu_name)


@app.route('/menu/submenu/<submenu_name>/delete', methods=['POST'])
async def delete_submenu(submenu_name):
    config = load_config()

    if 'menu' in config and submenu_name in config['menu']:
        del config['menu'][submenu_name]
        save_config(config)
        await flash(f'Submenu "{submenu_name}" deleted successfully!', 'success')
    else:
        await flash(f'Submenu "{submenu_name}" not found!', 'error')

    return redirect(url_for('menu'))


@app.route('/menu/submenu/<submenu_name>/edit/<int:item_num>', methods=['GET', 'POST'])
async def edit_submenu_item(submenu_name, item_num):  # Changed from item_index to item_num
    config = load_config()
    item_key = f'item_{item_num}'
    submenu_data = config.get('menu', {}).get(submenu_name, {})

    if item_key not in submenu_data:
        await flash('Item not found', 'error')
        return redirect(url_for('view_submenu', submenu_name=submenu_name))

    if request.method == 'POST':
        form = await request.form
        submenu_data[item_key] = {
            'name': form['name'],
            'cmd': form['cmd']
        }
        save_config(config)
        await flash('Item updated!', 'success')
        return redirect(url_for('view_submenu', submenu_name=submenu_name))

    return await render_template('menu/edit_item.html',
                                 submenu_name=submenu_name,
                                 item_num=item_num,
                                 item=submenu_data[item_key])


@app.route('/menu/submenu/<submenu_name>/delete/<int:item_num>', methods=['POST'])
async def delete_submenu_item(submenu_name, item_num):
    config = load_config()
    item_key = f'item_{item_num}'

    if 'menu' in config and submenu_name in config['menu'] and item_key in config['menu'][submenu_name]:
        # Get the item name for the flash message
        item_name = config['menu'][submenu_name][item_key].get('name', 'Unnamed item')

        # Delete the item
        del config['menu'][submenu_name][item_key]

        # Clean up empty submenus
        if not config['menu'][submenu_name]:
            del config['menu'][submenu_name]

        save_config(config)
        await flash(f'Deleted item "{item_name}" from {submenu_name}', 'success')
    else:
        await flash('Item not found!', 'error')

    return redirect(url_for('view_submenu', submenu_name=submenu_name))


@app.route('/menu/submenu/add', methods=['GET', 'POST'])
async def add_submenu():
    if request.method == 'POST':
        form = await request.form
        submenu_name = secure_filename(form['name'])

        config = load_config()
        if 'menu' not in config:
            config['menu'] = {}
        if submenu_name not in config['menu']:
            config['menu'][submenu_name] = {}
            save_config(config)
            await flash('Submenu created!', 'success')
            return redirect(url_for('view_submenu', submenu_name=submenu_name))

    return await render_template('menu/add_submenu.html')


# ======================
# Folders Routes
# ======================


@app.route('/folders')
async def folders():
    config = load_config()
    return await render_template('folders/index.html', folders=config.get('folders', {}))


@app.route('/folders/add', methods=['GET', 'POST'])
async def add_folder():
    if request.method == 'POST':
        form = await request.form
        config = load_config()

        folder_id = secure_filename(form['id'])
        config.setdefault('folders', {})[folder_id] = {
            'name': form['name'],
            'path': form['path'],
            'filemanager': form['filemanager'],
            'icon': form['icon']
        }

        save_config(config)
        await flash('Folder added!', 'success')
        return redirect(url_for('folders'))

    return await render_template('folders/add.html')


@app.route('/folders/edit/<folder_id>', methods=['GET', 'POST'])
async def edit_folder(folder_id):
    config = load_config()

    if request.method == 'POST':
        form = await request.form
        config['folders'][folder_id] = {
            'name': form['name'],
            'path': form['path'],
            'filemanager': form['filemanager'],
            'icon': form['icon']
        }
        save_config(config)
        await flash('Folder updated!', 'success')
        return redirect(url_for('folders'))

    folder = config.get('folders', {}).get(folder_id, {})
    return await render_template('folders/edit.html', folder_id=folder_id, folder=folder)


@app.route('/folders/delete/<folder_id>', methods=['POST'])
async def delete_folder(folder_id):
    config = load_config()
    if 'folders' in config and folder_id in config['folders']:
        del config['folders'][folder_id]
        save_config(config)
        await flash('Folder deleted!', 'success')
    return redirect(url_for('folders'))

# ======================
# Main Route
# ======================


@app.route('/')
async def index():
    config = load_config()
    config_path = app.config['CONFIG_PATH']
    last_modified = time.ctime(os.path.getmtime(config_path)) if os.path.exists(config_path) else "Never"
    return await render_template(
        'index.html',
        config=config,
        config_path=config_path,
        last_modified=last_modified
    )


class WebViewManager:
    def __init__(self, quart_app):
        self.quart_app = quart_app
        self.server_task = None
        self.window = None
        self.shutdown_event = asyncio.Event()

        # Set up logging
        self.setup_logging()

    def setup_logging(self):
        """Configure logging to show in console"""
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout)  # Output to console
            ]
        )
        self.logger = logging.getLogger('WebViewManager')
        self.logger.info("Logging initialized")

    async def run_server(self):
        try:
            self.logger.debug("Starting server...")
            config = Config()
            config.bind = ["127.0.0.1:5001"]
            config.use_reloader = False

            # Enable access logging
            config.accesslog = "-"  # Log to stdout
            config.errorlog = "-"   # Log to stdout

            await serve(self.quart_app, config, shutdown_trigger=self.shutdown_event.wait)
        except Exception as e:
            self.logger.error(f"Server error: {str(e)}")
            raise

    def start_server(self):
        try:
            self.logger.info("Creating new event loop")
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()
            self.server_task = loop.create_task(self.run_server())

            self.logger.info("Starting event loop")
            loop.run_forever()
        except Exception as e:
            self.logger.error(f"Event loop error: {str(e)}")
            raise
        finally:
            self.logger.info("Closing event loop")
            loop.close()

    def start(self):
        self.logger.info("Starting WebView manager")

        # Start server thread
        t = threading.Thread(target=self.start_server, daemon=True)
        t.start()
        self.logger.debug("Server thread started")

        # Wait for server to start
        time.sleep(1)

        try:
            self.logger.debug("Creating WebView window")
            self.window = webview.create_window(
                'Waypanel Settings',
                'http://127.0.0.1:5001',
                width=1200,
                height=800,
                min_size=(800, 600)
            )

            def on_closed():
                self.logger.info("Window closed, shutting down")
                self.shutdown_event.set()
                if self.server_task:
                    self.server_task.cancel()

            self.window.events.closed += on_closed

            self.logger.info("Starting WebView")
            webview.start()
        except Exception as e:
            self.logger.error(f"WebView error: {str(e)}")
            raise


def main():
    # Set up root logger
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    try:
        logger.info("Loading configuration")
        config = load_config()
        save_config(config)

        logger.info("Setting up signal handlers")
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        logger.info("Starting WebView manager")
        manager = WebViewManager(app)
        manager.start()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        raise


__all__ = ['Config', 'load_config', 'save_config']
