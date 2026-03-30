<?php
/**
 * Backyard Hummers — Guestbook API
 * Runs on SiteGround (PHP). Stores entries as JSON in data/guestbook_entries.json.
 * No database setup required.
 */

header('Content-Type: application/json');

// CORS — allowlist only
$allowed_origins = ['https://backyardhummers.com', 'http://localhost:5555'];
$req_origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if (in_array($req_origin, $allowed_origins, true)) {
    header('Access-Control-Allow-Origin: ' . $req_origin);
    header('Vary: Origin');
} elseif ($req_origin === '') {
    // Same-origin requests (no Origin header) — allow
    header('Access-Control-Allow-Origin: https://backyardhummers.com');
}
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

$entries_file = __DIR__ . '/data/guestbook_entries.json';

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
    // Atomic write via temp file
    $tmp = $file . '.tmp';
    file_put_contents($tmp, json_encode($entries, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
    rename($tmp, $file);
}

function ip_hash(string $ip): string {
    return substr(hash('sha256', $ip), 0, 16);
}

function content_hash(string $name, string $message): string {
    return substr(hash('sha256', strtolower($name . substr($message, 0, 100))), 0, 16);
}

function is_spam(string $text): bool {
    // URL patterns — no legitimate guestbook message needs links
    if (preg_match('/https?:\/\/|www\.|\.com\/|\.net\/|\.org\//i', $text)) return true;

    // Profanity (basic list, whole-word, case-insensitive)
    $banned = ['fuck','shit','ass','damn','bitch','dick','cunt','piss',
               'asshole','bastard','stfu','wtf','slut','whore'];
    $lower = strtolower($text);
    foreach ($banned as $w) {
        if (preg_match('/\b' . preg_quote($w, '/') . '\b/i', $lower)) return true;
    }
    return false;
}

// ------------------------------------------------------------------ GET — return entries

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $entries = load_entries($entries_file);
    // Filter to approved entries only, strip internal fields
    $public = [];
    foreach ($entries as $e) {
        if (($e['status'] ?? 'approved') !== 'approved') continue;
        $public[] = [
            'id'        => $e['id'] ?? '',
            'name'      => $e['name'] ?? '',
            'message'   => $e['message'] ?? '',
            'timestamp' => $e['timestamp'] ?? '',
        ];
    }
    echo json_encode(['ok' => true, 'entries' => $public, 'total' => count($public)]);
    exit;
}

// ------------------------------------------------------------------ POST — add entry

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    // Origin validation (CSRF prevention)
    $origin  = $_SERVER['HTTP_ORIGIN'] ?? '';
    $referer = $_SERVER['HTTP_REFERER'] ?? '';
    $allowed_host = 'backyardhummers.com';
    if ($origin !== '' && !str_contains($origin, $allowed_host)) {
        // Origin header present but doesn't match — reject
        if (!str_contains($referer, $allowed_host) && !str_contains($origin, 'localhost')) {
            http_response_code(403);
            echo json_encode(['ok' => false, 'error' => 'Forbidden.']);
            exit;
        }
    }

    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'Invalid JSON.']);
        exit;
    }

    // Honeypot — bots that fill hidden "website" field get rejected
    if (!empty($body['website'] ?? '')) {
        http_response_code(422);
        echo json_encode(['ok' => false, 'error' => 'Validation failed.']);
        exit;
    }

    // Timing challenge — reject submissions faster than 3 seconds
    $elapsed = intval($body['_t'] ?? 0);
    if ($elapsed < 3000) {
        http_response_code(422);
        echo json_encode(['ok' => false, 'error' => 'Please take your time.']);
        exit;
    }

    $name    = substr(trim(strip_tags($body['name']    ?? '')), 0, 50);
    $message = substr(trim(strip_tags($body['message'] ?? '')), 0, 500);

    if ($name === '' || $message === '') {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'Name and message are required.']);
        exit;
    }

    // Content filtering — spam and profanity
    if (is_spam($name) || is_spam($message)) {
        http_response_code(422);
        echo json_encode(['ok' => false, 'error' => 'Your message could not be posted.']);
        exit;
    }

    // Rate limit: max 3 entries per IP per hour
    $ip   = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
    $hash = ip_hash($ip);
    $entries = load_entries($entries_file);
    $cutoff  = time() - 3600;
    $recent  = array_filter($entries, fn($e) => ($e['ip_hash'] ?? '') === $hash && ($e['ts'] ?? 0) > $cutoff);

    if (count($recent) >= 3) {
        http_response_code(429);
        echo json_encode(['ok' => false, 'error' => 'Too many entries. Try again later.']);
        exit;
    }

    // Global rate limit: max 20 entries per hour site-wide
    $global_recent = array_filter($entries, fn($e) => ($e['ts'] ?? 0) > $cutoff);
    if (count($global_recent) >= 20) {
        http_response_code(429);
        echo json_encode(['ok' => false, 'error' => 'Guestbook is busy. Try again later.']);
        exit;
    }

    // Duplicate detection: reject identical content within 24 hours
    $c_hash = content_hash($name, $message);
    $day_cutoff = time() - 86400;
    $dupes = array_filter($entries, fn($e) => ($e['content_hash'] ?? '') === $c_hash && ($e['ts'] ?? 0) > $day_cutoff);
    if (count($dupes) > 0) {
        http_response_code(429);
        echo json_encode(['ok' => false, 'error' => 'Duplicate entry.']);
        exit;
    }

    $entry = [
        'id'           => date('Ymd-His') . '-' . substr(str_shuffle('abcdefghijklmnopqrstuvwxyz'), 0, 4),
        'name'         => $name,
        'message'      => $message,
        'timestamp'    => date('Y-m-d H:i:s'),
        'ts'           => time(),
        'ip_hash'      => $hash,
        'content_hash' => $c_hash,
        'status'       => 'approved',
    ];

    // Newest first, keep last 500
    array_unshift($entries, $entry);
    $entries = array_slice($entries, 0, 500);
    save_entries($entries_file, $entries);

    echo json_encode(['ok' => true, 'id' => $entry['id']]);
    exit;
}

http_response_code(405);
echo json_encode(['ok' => false, 'error' => 'Method not allowed.']);
