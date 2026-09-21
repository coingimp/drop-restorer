<?php
if (!defined('ABSPATH')) { exit; }

function dr_seo_policy() { return isset(dr_site()['seo_policy']) ? dr_site()['seo_policy'] : array(); }
// Retained for compatibility with existing snippets; editorial checks never close pages.
function dr_pending_casino($post_id, $page = null) { return false; }
function dr_owned_post($key) {
    $posts = get_posts(array('post_type'=>'page','post_status'=>'publish','meta_key'=>'_dr_key','meta_value'=>$key,'numberposts'=>2));
    return count($posts) === 1 ? $posts[0] : null;
}
function dr_seo_xml() {
    $xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">';
    foreach (dr_site()['pages'] as $key => $page) {
        $post = dr_owned_post($key);
        if (!$post) { continue; }
        $xml .= '<url><loc>' . esc_xml(dr_site()['origin'] . $page['route']) . '</loc></url>';
    }
    return $xml . '</urlset>';
}
add_filter('wp_sitemaps_enabled', function($enabled) { return dr_seo_policy() ? false : $enabled; });
// The generated package is public from activation/import onward.
function dr_enable_indexation() { update_option('blog_public', '1'); }
add_action('after_switch_theme', 'dr_enable_indexation');
add_action('import_end', 'dr_enable_indexation');
add_filter('robots_txt', function($text, $public) {
    if (!dr_seo_policy()) { return $text; }
    return "User-agent: *\nAllow: /\nSitemap: " . dr_site()['origin'] . "/sitemap.xml\n";
}, PHP_INT_MAX, 2);
add_filter('wp_robots', function($robots) {
    if (dr_page()) {
        // A restored page is public regardless of the old archive policy or
        // the WordPress Reading setting. Keep only the positive directives so
        // a plugin cannot leave a second conflicting value behind.
        return array('index' => true, 'follow' => true);
    }
    return $robots;
}, PHP_INT_MAX);
add_filter('wpseo_robots', function($robots) {
    return dr_page() ? 'index, follow' : $robots;
}, PHP_INT_MAX);
function dr_public_plugin_robots($robots) {
    if (dr_page()) {
        // SEO plugins use both a string and an array contract. Normalize both
        // forms rather than assuming associative array keys.
        return is_string($robots) ? 'index, follow' : (is_array($robots) ? array('index' => 'index', 'follow' => 'follow') : $robots);
    }
    return $robots;
}
foreach (array('wpseo_robots_array', 'rank_math/frontend/robots', 'aioseo_robots_meta') as $hook) {
    add_filter($hook, 'dr_public_plugin_robots', PHP_INT_MAX);
}

// A final head guard catches a plugin that prints its own robot meta tag after
// the normal WordPress filters. The archive sanitizer handles saved page HTML;
// this guard handles runtime output on the installed site as well.
function dr_strip_public_robot_markup($html) {
    if (!dr_page() || !is_string($html)) { return $html; }
    $html = preg_replace_callback('/<meta\b[^>]*>/i', function($match) {
        return preg_match('/\b(?:name|property|http-equiv)\s*=\s*["\']?(?:x-robots-tag|[a-z0-9:_-]*robots[a-z0-9:_-]*|[a-z0-9_-]*bot[a-z0-9_-]*)["\']?/i', $match[0]) ? '' : $match[0];
    }, $html);
    $html = preg_replace('/<\/?noindex\b[^>]*>|<!--[\s]*(?:\/?noindex|google(?:off|on)\s*:\s*index)[\s]*-->/i', '', $html);
    return $html;
}
add_action('wp_head', function() {
    if (dr_page()) {
        if (function_exists('header_remove')) { header_remove('X-Robots-Tag'); }
        $GLOBALS['dr_public_head_buffer_level'] = ob_get_level();
        ob_start('dr_strip_public_robot_markup');
    }
}, 0);
add_action('wp_head', function() {
    $level = $GLOBALS['dr_public_head_buffer_level'] ?? null;
    if ($level !== null && ob_get_level() > $level) {
        if (function_exists('header_remove')) { header_remove('X-Robots-Tag'); }
        ob_end_flush();
        unset($GLOBALS['dr_public_head_buffer_level']);
    }
}, PHP_INT_MAX);

// Host and path redirects run before the restored-page router and WP guessing.
add_action('template_redirect', function() {
    if (!dr_seo_policy() || is_admin() || wp_doing_ajax() || is_preview()) { return; }
    $request = isset($_SERVER['REQUEST_URI']) ? wp_unslash($_SERVER['REQUEST_URI']) : '/';
    $path = strtok($request, '?');
    if (strpos($path, '/go/') === 0) { return; }
    $policy = dr_seo_policy(); $target = '';
    if (isset($policy['redirects'][$request])) { $target = $policy['redirects'][$request]; }
    if (!$target && isset($policy['redirects'][$path])) { $target = $policy['redirects'][$path]; }
    $selected = false;
    foreach (dr_site()['pages'] as $key => $page) {
        if ($request === $page['route']) { $selected = true; }
        if ($path === $page['route'] && $request !== $path) { $target = $page['route']; }
        if (isset($_GET['page_id']) && (int)$_GET['page_id'] > 0 && get_post_meta((int)$_GET['page_id'], '_dr_key', true) === $key) { $target = $page['route']; }
    }
    $desired = dr_site()['origin']; $runtime_host = wp_parse_url(home_url(), PHP_URL_HOST);
    $local = in_array($runtime_host, array('127.0.0.1','localhost','::1'), true);
    $host = isset($_SERVER['HTTP_HOST']) ? strtolower($_SERVER['HTTP_HOST']) : '';
    $actual = (is_ssl() ? 'https://' : 'http://') . $host;
    if (!$local && $actual !== $desired && ($selected || $target || in_array($path,array('/robots.txt','/sitemap.xml'),true))) {
        wp_redirect($desired . ($target ?: $path), 301, 'Site URL policy'); exit;
    }
    if ($target && $request !== $target) { wp_safe_redirect(home_url($target), 301, 'Site URL policy'); exit; }
    if ($path === '/sitemap.xml') { status_header(200); header('Content-Type: application/xml; charset=UTF-8'); echo dr_seo_xml(); exit; }
    if ($path === '/robots.txt') { return; }
    if (!$selected) {
        // Prevent default posts, search, attachments and plugin routes becoming
        // unintended indexed pages. REST/admin retain their normal operation.
        if (strpos($path, '/wp-admin') === 0 || strpos($path, '/wp-json/') === 0 || $path === '/wp-login.php') { return; }
        $GLOBALS['wp_query']->set_404(); status_header(404); nocache_headers();
        include get_template_directory() . '/404.php'; exit;
    }
}, -30);

add_action('add_meta_boxes_page', function($post) {
    if (get_post_meta($post->ID, '_dr_casino', true) !== '1') { return; }
    add_meta_box('dr-indexation','Индексация страницы', function($post) {
        echo '<p>Опубликованная страница доступна для индексации и включена в sitemap. Заполните контент, title и description. Проверки качества не закрывают страницу от индексации.</p>';
        echo '<p>Таблица: <code>[casino_table id="88"]</code>. Компактная высота конкретной таблицы: <code>[casino_table id="88" row_height="110"]</code>, где допустимо целое значение от 72 до 240 пикселей. Общая ширина казино-контента, таблицы, колонок и высота строк задаются в инструменте на этапе «Страницы и метаданные». Проверка брендов и редиректов: Внешний вид → Партнёрские ссылки.</p>';
    }, 'page');
});
