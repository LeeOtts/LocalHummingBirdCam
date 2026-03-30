<?php
/**
 * Backyard Hummers — Guestbook API
 * Runs on SiteGround (PHP). Stores entries as JSON in data/guestbook_entries.json.
 * No database setup required.
 */

header('Content-Type: application/json');
header('Access-Control-Allow-Origin: *');
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

// ------------------------------------------------------------------ GET — return entries

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $entries = load_entries($entries_file);
    // Strip internal IP field before sending to client
    $public = array_map(function($e) {
        return [
            'id'        => $e['id'] ?? '',
            'name'      => $e['name'] ?? '',
            'message'   => $e['message'] ?? '',
            'timestamp' => $e['timestamp'] ?? '',
        ];
    }, $entries);
    echo json_encode(['ok' => true, 'entries' => $public, 'total' => count($public)]);
    exit;
}

// ------------------------------------------------------------------ POST — add entry

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'Invalid JSON.']);
        exit;
    }

    $name    = substr(trim(strip_tags($body['name']    ?? '')), 0, 50);
    $message = substr(trim(strip_tags($body['message'] ?? '')), 0, 500);

    if ($name === '' || $message === '') {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'Name and message are required.']);
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

    $entry = [
        'id'        => date('Ymd-His') . '-' . substr(str_shuffle('abcdefghijklmnopqrstuvwxyz'), 0, 4),
        'name'      => $name,
        'message'   => $message,
        'timestamp' => date('Y-m-d H:i:s'),
        'ts'        => time(),
        'ip_hash'   => $hash,
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
