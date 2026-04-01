<?php
header('Content-Type: application/json');
header('Cache-Control: no-cache, no-store, must-revalidate');
header('Pragma: no-cache');
header('Expires: 0');
header('Vary: *');
header('Surrogate-Control: no-store');
readfile(__DIR__ . '/site_data.json');
