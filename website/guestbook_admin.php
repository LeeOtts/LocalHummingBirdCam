<?php
/**
 * Backyard Hummers — Guestbook Admin Panel
 * Simple session-based auth for managing guestbook entries.
 */
session_start();

$config_file = __DIR__ . '/data/.guestbook_config.php';
$entries_file = __DIR__ . '/data/guestbook_entries.json';
$attempts_file = __DIR__ . '/data/.login_attempts.json';

// Load config
if (!file_exists($config_file)) {
    die('Admin not configured. Create data/.guestbook_config.php with $GUESTBOOK_ADMIN_HASH.');
}
require_once $config_file;

// ------------------------------------------------------------------ helpers

function load_entries(string $file): array {
    if (!file_exists($file)) return [];
    $raw = file_get_contents($file);
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function save_entries(string $file, array $entries): void {
    $dir = dirname($file);
    if (!is_dir($dir)) mkdir($dir, 0755, true);
    $tmp = $file . '.tmp';
    file_put_contents($tmp, json_encode($entries, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    rename($tmp, $file);
}

function is_logged_in(): bool {
    return !empty($_SESSION['gb_admin_auth']);
}

function h(string $s): string {
    return htmlspecialchars($s, ENT_QUOTES | ENT_HTML5, 'UTF-8');
}

// --------------------------------------------------------- rate limiting

define('MAX_ATTEMPTS', 5);
define('LOCKOUT_SECONDS', 900); // 15 minutes
define('DELAY_AFTER', 3);      // sleep(2) after this many failures

function load_attempts(string $file): array {
    if (!file_exists($file)) return [];
    $raw = file_get_contents($file);
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function save_attempts(string $file, array $data): void {
    $dir = dirname($file);
    if (!is_dir($dir)) mkdir($dir, 0755, true);
    file_put_contents($file, json_encode($data, JSON_PRETTY_PRINT));
}

function get_client_ip(): string {
    return $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';
}

function check_lockout(string $file): array {
    $attempts = load_attempts($file);
    $ip = get_client_ip();
    $now = time();

    // Purge expired entries
    foreach ($attempts as $k => $v) {
        if ($now - ($v['last'] ?? 0) > LOCKOUT_SECONDS) {
            unset($attempts[$k]);
        }
    }
    save_attempts($file, $attempts);

    $record = $attempts[$ip] ?? null;
    if ($record && ($record['count'] ?? 0) >= MAX_ATTEMPTS) {
        $remaining = LOCKOUT_SECONDS - ($now - $record['last']);
        if ($remaining > 0) {
            return ['locked' => true, 'minutes' => (int)ceil($remaining / 60), 'count' => $record['count']];
        }
    }
    return ['locked' => false, 'count' => $record['count'] ?? 0];
}

function record_failure(string $file): void {
    $attempts = load_attempts($file);
    $ip = get_client_ip();
    if (!isset($attempts[$ip])) {
        $attempts[$ip] = ['count' => 0, 'last' => 0];
    }
    $attempts[$ip]['count']++;
    $attempts[$ip]['last'] = time();
    save_attempts($file, $attempts);

    if ($attempts[$ip]['count'] >= DELAY_AFTER) {
        sleep(2);
    }
}

function clear_attempts(string $file): void {
    $attempts = load_attempts($file);
    $ip = get_client_ip();
    unset($attempts[$ip]);
    save_attempts($file, $attempts);
}

// ------------------------------------------------------------------ actions

$action = $_GET['action'] ?? $_POST['action'] ?? '';
$flash = '';

// Login
if ($action === 'login' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $lockout = check_lockout($attempts_file);
    if ($lockout['locked']) {
        $flash = 'Too many attempts. Try again in ' . $lockout['minutes'] . ' minute' . ($lockout['minutes'] === 1 ? '' : 's') . '.';
    } else {
        $password = $_POST['password'] ?? '';
        if (password_verify($password, $GUESTBOOK_ADMIN_HASH)) {
            clear_attempts($attempts_file);
            $_SESSION['gb_admin_auth'] = true;
            header('Location: guestbook_admin.php');
            exit;
        } else {
            record_failure($attempts_file);
            $lockout = check_lockout($attempts_file);
            if ($lockout['locked']) {
                $flash = 'Too many attempts. Try again in ' . $lockout['minutes'] . ' minute' . ($lockout['minutes'] === 1 ? '' : 's') . '.';
            } else {
                $flash = 'Invalid password.';
            }
        }
    }
}

// Logout
if ($action === 'logout') {
    session_destroy();
    header('Location: guestbook_admin.php');
    exit;
}

// Delete entry (soft-delete)
if ($action === 'delete' && is_logged_in() && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $id = $_POST['id'] ?? '';
    if ($id !== '') {
        $entries = load_entries($entries_file);
        foreach ($entries as &$e) {
            if (($e['id'] ?? '') === $id) {
                $e['status'] = 'deleted';
                break;
            }
        }
        unset($e);
        save_entries($entries_file, $entries);
        $flash = 'Entry deleted.';
    }
}

// Restore entry
if ($action === 'restore' && is_logged_in() && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $id = $_POST['id'] ?? '';
    if ($id !== '') {
        $entries = load_entries($entries_file);
        foreach ($entries as &$e) {
            if (($e['id'] ?? '') === $id) {
                $e['status'] = 'approved';
                break;
            }
        }
        unset($e);
        save_entries($entries_file, $entries);
        $flash = 'Entry restored.';
    }
}

// ------------------------------------------------------------------ render

$show_deleted = isset($_GET['show_deleted']);
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Guestbook Admin - Backyard Hummers</title>
    <meta name="robots" content="noindex, nofollow">
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1a12; color: #e8f0ea; padding: 20px; line-height: 1.5; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #d4a017; margin-bottom: 20px; font-size: 1.5em; }
        .flash { background: #1e3625; border: 1px solid #2a4d33; padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; color: #6dbf5a; }
        .flash.err { color: #e74c3c; border-color: #c0392b; }

        /* Login */
        .login-box { max-width: 320px; margin: 80px auto; }
        .login-box input { width: 100%; padding: 10px 14px; margin-bottom: 12px; background: #152419; border: 1px solid #2a4d33; border-radius: 6px; color: #e8f0ea; font-size: 1em; }
        .login-box input:focus { outline: none; border-color: #d4a017; }

        /* Buttons */
        .btn { display: inline-block; padding: 8px 16px; border: none; border-radius: 6px; font-size: 0.85em; cursor: pointer; text-decoration: none; }
        .btn-gold { background: #d4a017; color: #0f1923; font-weight: 700; }
        .btn-gold:hover { background: #f0c54d; }
        .btn-red { background: #c0392b; color: #fff; }
        .btn-red:hover { background: #e74c3c; }
        .btn-green { background: #3d7a35; color: #fff; }
        .btn-green:hover { background: #6dbf5a; }
        .btn-sm { padding: 5px 12px; font-size: 0.8em; }
        .btn-link { background: none; color: #a3b8a6; text-decoration: underline; border: none; cursor: pointer; font-size: 0.85em; }

        /* Entry table */
        .entry { background: #152419; border: 1px solid #1e3625; border-radius: 6px; padding: 14px 18px; margin-bottom: 8px; }
        .entry.deleted { opacity: 0.5; border-style: dashed; }
        .entry-meta { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
        .entry-name { font-weight: 700; color: #f0c54d; }
        .entry-time { font-size: 0.8em; color: #5a7360; }
        .entry-ip { font-size: 0.75em; color: #5a7360; font-family: monospace; }
        .entry-msg { color: #e8f0ea; font-size: 0.95em; margin-bottom: 8px; word-break: break-word; }
        .entry-actions { display: flex; gap: 6px; }
        .toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
        .count { color: #a3b8a6; font-size: 0.9em; }
    </style>
</head>
<body>
<div class="container">

<?php if (!is_logged_in()): ?>
    <!-- Login Form -->
    <div class="login-box">
        <h1>Guestbook Admin</h1>
        <?php if ($flash): ?><div class="flash err"><?= h($flash) ?></div><?php endif; ?>
        <form method="POST">
            <input type="hidden" name="action" value="login">
            <input type="password" name="password" placeholder="Admin password" autofocus required>
            <button type="submit" class="btn btn-gold" style="width:100%">Log In</button>
        </form>
    </div>

<?php else: ?>
    <!-- Admin Panel -->
    <div class="toolbar">
        <h1>Guestbook Admin</h1>
        <div>
            <?php if ($show_deleted): ?>
                <a href="guestbook_admin.php" class="btn-link">Hide deleted</a>
            <?php else: ?>
                <a href="guestbook_admin.php?show_deleted=1" class="btn-link">Show deleted</a>
            <?php endif; ?>
            <a href="guestbook_admin.php?action=logout" class="btn btn-sm btn-red" style="margin-left:8px;">Logout</a>
        </div>
    </div>

    <?php if ($flash): ?><div class="flash"><?= h($flash) ?></div><?php endif; ?>

    <?php
    $entries = load_entries($entries_file);
    $visible = $show_deleted ? $entries : array_filter($entries, fn($e) => ($e['status'] ?? 'approved') === 'approved');
    $approved_count = count(array_filter($entries, fn($e) => ($e['status'] ?? 'approved') === 'approved'));
    $deleted_count = count($entries) - $approved_count;
    ?>

    <p class="count"><?= $approved_count ?> approved, <?= $deleted_count ?> deleted, <?= count($entries) ?> total</p>

    <?php foreach ($visible as $e): ?>
        <?php $status = $e['status'] ?? 'approved'; ?>
        <div class="entry <?= $status === 'deleted' ? 'deleted' : '' ?>">
            <div class="entry-meta">
                <span>
                    <span class="entry-name"><?= h($e['name'] ?? '') ?></span>
                    <span class="entry-time"><?= h($e['timestamp'] ?? '') ?></span>
                </span>
                <span class="entry-ip"><?= h($e['ip_hash'] ?? '—') ?></span>
            </div>
            <div class="entry-msg"><?= h($e['message'] ?? '') ?></div>
            <div class="entry-actions">
                <?php if ($status === 'approved'): ?>
                    <form method="POST" style="display:inline">
                        <input type="hidden" name="action" value="delete">
                        <input type="hidden" name="id" value="<?= h($e['id'] ?? '') ?>">
                        <button type="submit" class="btn btn-sm btn-red" onclick="return confirm('Delete this entry?')">Delete</button>
                    </form>
                <?php else: ?>
                    <form method="POST" style="display:inline">
                        <input type="hidden" name="action" value="restore">
                        <input type="hidden" name="id" value="<?= h($e['id'] ?? '') ?>">
                        <button type="submit" class="btn btn-sm btn-green">Restore</button>
                    </form>
                <?php endif; ?>
            </div>
        </div>
    <?php endforeach; ?>

    <?php if (empty($visible)): ?>
        <p style="text-align:center; color:#5a7360; padding:40px;">No entries.</p>
    <?php endif; ?>

<?php endif; ?>

</div>
</body>
</html>
