<?php
if (!defined('ABSPATH')) { exit; }

function dr_site() {
    static $site = null;
    if ($site === null) {
        $site = json_decode(file_get_contents(get_template_directory() . '/site.json'), true);
        // The archive may have used HTTP or a different hostname. After install,
        // WordPress's configured public address owns redirects and canonicals.
        // Read the option: home_url() can change its scheme for this request.
        $home = untrailingslashit((string) get_option('home'));
        $host = strtolower((string) wp_parse_url($home, PHP_URL_HOST));
        $scheme = wp_parse_url($home, PHP_URL_SCHEME);
        if (is_array($site) && $host && in_array($scheme, array('http', 'https'), true)
            && !in_array($host, array('127.0.0.1', 'localhost', '::1', '[::1]'), true)) {
            $site['origin'] = $home;
        }
    }
    return $site ?: array('pages' => array(), 'lang' => 'en', 'origin' => home_url());
}
require_once __DIR__ . '/widget.php';
require_once __DIR__ . '/seo.php';
require_once __DIR__ . '/article.php';
function dr_key() {
    return isset($GLOBALS['dr_current_key']) ? $GLOBALS['dr_current_key'] : get_post_meta(get_queried_object_id(), '_dr_key', true);
}
function dr_page() {
    $site = dr_site(); $key = dr_key();
    return isset($site['pages'][$key]) ? $site['pages'][$key] : null;
}
function dr_casino_layout() {
    $defaults = array('content_width_px'=>1120, 'table_width_px'=>0, 'row_height_px'=>110,
                      'cell_padding_px'=>12, 'cell_widths_px'=>array('logo'=>0,'bonus'=>0,'characteristics'=>0,'rating'=>0,'button'=>0));
    $configured = dr_site()['seo_policy']['casino_layout'] ?? array();
    if (!is_array($configured)) { $configured = array(); }
    foreach (array('content_width_px','table_width_px','row_height_px','cell_padding_px') as $key) {
        if (isset($configured[$key]) && is_numeric($configured[$key])) { $defaults[$key] = (int)$configured[$key]; }
    }
    $defaults['content_width_px'] = max(0,min(2400,$defaults['content_width_px']));
    $defaults['table_width_px'] = max(0,min(2400,$defaults['table_width_px']));
    $defaults['row_height_px'] = max(72,min(400,$defaults['row_height_px']));
    $defaults['cell_padding_px'] = max(0,min(32,$defaults['cell_padding_px']));
    $widths = isset($configured['cell_widths_px']) && is_array($configured['cell_widths_px']) ? $configured['cell_widths_px'] : array();
    foreach ($defaults['cell_widths_px'] as $key => $value) {
        if (isset($widths[$key]) && is_numeric($widths[$key])) { $defaults['cell_widths_px'][$key] = max(0,min(1200,(int)$widths[$key])); }
    }
    return $defaults;
}
function dr_casino_layout_css() {
    $layout = dr_casino_layout();
    $content = $layout['content_width_px'] ? $layout['content_width_px'].'px' : 'none';
    $table = $layout['table_width_px'] ? $layout['table_width_px'].'px' : '100%';
    $columns = array('logo'=>'.casino-row__logo','bonus'=>'.casino-row__bonus','characteristics'=>'.casino-row__characteristics','rating'=>'.casino-row__rating','button'=>'.casino-row__button');
    $css = 'body.dr-casino-page .dr-casino-content{box-sizing:border-box!important;width:100%!important;max-width:'.$content.'!important;margin-left:auto!important;margin-right:auto!important;}body.dr-casino-page .dr-legacy-layout-table{box-sizing:border-box;width:100%!important;max-width:100%!important;}body.dr-casino-page.dr-legacy-table-casino .dr-legacy-layout-table{width:auto!important;max-width:100%!important;margin-left:auto!important;margin-right:auto!important;}body.dr-casino-page.dr-legacy-table-casino .dr-casino-content{width:100%!important;max-width:100%!important;}'
         . 'body.dr-casino-page .dr-casino-content .dr-casino-table{--dr-casino-table-width:'.$table.';--dr-offer-row-height:'.$layout['row_height_px'].'px;--dr-casino-cell-padding:'.$layout['cell_padding_px'].'px;}'
         . 'body.dr-casino-page .dr-casino-content .dr-casino-table .casino-table tbody tr{min-height:var(--dr-offer-row-height);}'
         . 'body.dr-casino-page .dr-casino-content .dr-casino-table .casino-table tbody tr td{padding-top:var(--dr-casino-cell-padding)!important;padding-bottom:var(--dr-casino-cell-padding)!important;}';
    foreach ($columns as $key => $selector) {
        if ($layout['cell_widths_px'][$key]) { $css .= 'body.dr-casino-page .dr-casino-content .dr-casino-table '.$selector.'{width:'.$layout['cell_widths_px'][$key].'px!important;}'; }
    }
    $mobile = '';
    foreach ($columns as $key => $selector) {
        if ($layout['cell_widths_px'][$key]) { $mobile .= 'body.dr-casino-page .dr-casino-content .dr-casino-table '.$selector.'{width:auto!important;}'; }
    }
    return '<style id="dr-casino-layout">'.$css.'@media(max-width:768px){body.dr-casino-page .dr-casino-content{max-width:100%!important;}body.dr-casino-page .dr-casino-content .dr-casino-table{width:100%!important;}body.dr-casino-page.dr-legacy-table-casino .dr-legacy-layout-table{width:100%!important;max-width:100%!important;}'.$mobile.'}</style>';
}
function dr_has_seo_plugin() {
    return defined('WPSEO_VERSION') || defined('RANK_MATH_VERSION') || defined('AIOSEO_VERSION');
}
function dr_imported_menu() {
    $menu = wp_get_nav_menu_object('dr-primary');
    if (!$menu) { return null; }
    $items = wp_get_nav_menu_items($menu->term_id);
    return is_array($items) && count($items) > 0 ? $menu : null;
}
function dr_asset_urls($html) {
    $html = preg_replace_callback('/<!--DR_MENU_START-->.*?<!--DR_MENU_END-->/s', function($match) {
        // Do not trust an existing location assignment: on an already used
        // WordPress install it can point to an empty or unrelated menu. In
        // that case replacing the saved navigation would leave an empty UL.
        $menu = dr_imported_menu();
        if (!$menu) { return $match[0]; }
        $rendered = wp_nav_menu(array('menu' => $menu->term_id, 'container' => false, 'echo' => false,
                                      'fallback_cb' => false, 'items_wrap' => '%3$s', 'walker' => new DR_Menu_Walker()));
        return is_string($rendered) && trim($rendered) !== '' ? $rendered : $match[0];
    }, $html);
    return str_replace('/assets/', esc_url(get_template_directory_uri()) . '/assets/', $html);
}
function dr_permalink($url, $post_id = 0) {
    $key = get_post_meta($post_id, '_dr_key', true); $site = dr_site();
    return isset($site['pages'][$key]) ? home_url($site['pages'][$key]['route']) : $url;
}
add_filter('page_link', 'dr_permalink', 10, 2);
add_action('after_setup_theme', function() {
    add_theme_support('title-tag'); add_theme_support('post-thumbnails');
    register_nav_menus(array('dr-primary' => 'Основное меню'));
});
function dr_assign_menu() {
    $menu = wp_get_nav_menu_object('dr-primary');
    $locations = get_theme_mod('nav_menu_locations', array());
    // Always bind the package menu. A pre-existing theme can already have a
    // non-empty value here, but it may refer to another site's menu and would
    // otherwise make the generated navigation disappear.
    if ($menu && (int) ($locations['dr-primary'] ?? 0) !== (int) $menu->term_id) {
        $locations['dr-primary'] = $menu->term_id;
        set_theme_mod('nav_menu_locations', $locations);
    }
}
add_action('after_switch_theme', 'dr_assign_menu');
add_action('import_end', 'dr_assign_menu');

// Pretty package URLs need the web server's WordPress front controller even
// when the source archive used index.php query URLs. Prepare it once in admin.
function dr_schedule_routing_setup() {
    update_option('dr_routing_setup_pending', 1, false);
}
add_action('after_switch_theme', 'dr_schedule_routing_setup');
add_action('import_end', 'dr_schedule_routing_setup');
function dr_setup_routing() {
    if (!current_user_can('manage_options') || !get_option('dr_routing_setup_pending')) { return; }
    global $wp_rewrite;
    if ((string) get_option('permalink_structure') === '') {
        $site = dr_site();
        $style = isset($site['seo_policy']['url_style']) ? $site['seo_policy']['url_style'] : 'slash';
        $wp_rewrite->set_permalink_structure($style === 'no_slash' ? '/%postname%' : '/%postname%/');
    }
    // Core preserves rules outside its own .htaccess block. Do not flush on
    // ordinary page requests or replace an existing custom permalink choice.
    flush_rewrite_rules(true);
    delete_option('dr_routing_setup_pending');
}
// Finish the repair in the same request that activated the theme or imported
// the WXR. Waiting for a later admin_init leaves the first public pretty URL
// exposed to Apache before WordPress has written its front-controller rules.
function dr_prepare_routing_setup() {
    dr_schedule_routing_setup();
    if (current_user_can('manage_options')) { dr_setup_routing(); }
}
add_action('after_switch_theme', 'dr_prepare_routing_setup', 20);
add_action('import_end', 'dr_prepare_routing_setup', 20);
add_action('admin_init', 'dr_setup_routing');
// Register exact package routes before WordPress builds its rewrite rules.
// Casino pages deliberately keep the visible ``/casino/article/`` path while
// their imported WP pages use a leaf slug.  Without an explicit rule Apache
// can hand the nested path to WP as an unresolved page and the result is a
// front-end 404 even though the package manifest contains the page.
add_filter('query_vars', function($vars) {
    $vars[] = 'dr_restored_key';
    return $vars;
});
add_action('init', function() {
    foreach ((array) (dr_site()['pages'] ?? array()) as $key => $page) {
        $route = (string) ($page['route'] ?? '');
        $path = wp_parse_url($route, PHP_URL_PATH);
        if (!$path || $path === '/' || strpos($route, '?') !== false) { continue; }
        $pattern = trim(rawurldecode($path), '/');
        if ($pattern === '') { continue; }
        add_rewrite_rule('^' . preg_quote($pattern, '#') . '/?$',
                         'index.php?dr_restored_key=' . rawurlencode((string) $key), 'top');
    }
});
add_action('admin_notices', function() {
    if (!current_user_can('manage_options') || (string) get_option('permalink_structure') !== '') { return; }
    echo '<div class="notice notice-error"><p>Тема сайта: режим постоянных ссылок Plain несовместим с адресами страниц и /go/. '
        . '<a href="' . esc_url(admin_url('options-permalink.php')) . '">Выберите «Название записи» и сохраните постоянные ссылки</a>. '
        . 'После этого проверьте казино-страницы на самом домене.</p></div>';
});
class DR_Menu_Walker extends Walker_Nav_Menu {
    public function start_lvl(&$output, $depth = 0, $args = null) { $output .= '<ul class="dr-submenu">'; }
    public function end_lvl(&$output, $depth = 0, $args = null) { $output .= '</ul>'; }
    public function start_el(&$output, $item, $depth = 0, $args = null, $id = 0) {
        $children = in_array('menu-item-has-children', (array) $item->classes, true);
        $classes = array_map('sanitize_html_class', (array) $item->classes);
        if ($children) { $classes[] = 'dr-casino'; }
        $output .= '<li class="' . esc_attr(implode(' ', $classes)) . '">';
        if ($children) { $output .= '<button type="button" class="dr-casino-toggle" aria-expanded="false">' . esc_html($item->title) . '</button>'; }
        else { $output .= '<a href="' . esc_url($item->url) . '">' . esc_html($item->title) . '</a>'; }
    }
    public function end_el(&$output, $item, $depth = 0, $args = null) { $output .= '</li>'; }
}

// Only imported pages owned by this package participate in archive routing.
add_action('template_redirect', function() {
    if (is_admin() || is_feed() || is_preview() || wp_doing_ajax()) { return; }
    $request = isset($_SERVER['REQUEST_URI']) ? wp_unslash($_SERVER['REQUEST_URI']) : '/';
    $path = strtok($request, '?');
    $routes = dr_site()['pages'];
    uasort($routes, function($a, $b) { return (int) (strpos($a['route'], '?') === false) - (int) (strpos($b['route'], '?') === false); });
    $rewritten_key = sanitize_text_field((string) get_query_var('dr_restored_key'));
    foreach ($routes as $key => $page) {
        $route = $page['route'];
        $matches = $rewritten_key !== '' ? hash_equals((string) $key, $rewritten_key)
            : (strpos($route, '?') !== false ? $request === $route : rawurldecode($path) === rawurldecode($route));
        if (!$matches) { continue; }
        $posts = get_posts(array('post_type' => 'page', 'post_status' => 'publish', 'meta_key' => '_dr_key', 'meta_value' => $key, 'numberposts' => 2));
        if (count($posts) !== 1) { return; }
        $GLOBALS['dr_current_key'] = $key;
        $GLOBALS['wp_query'] = new WP_Query(array('page_id' => $posts[0]->ID));
        $GLOBALS['wp_the_query'] = $GLOBALS['wp_query'];
        status_header(200);
        // Remove an earlier PHP robots header once an owned public page is resolved.
        header_remove('X-Robots-Tag');
        remove_action('template_redirect', 'redirect_canonical');
        include get_template_directory() . ($page['casino'] ? '/casino-page.php' : '/page-template.php');
        exit;
    }
}, -10);

function dr_canonical($url = '') {
    $page = dr_page();
    return $page ? dr_site()['origin'] . $page['route'] : $url;
}
add_filter('wpseo_canonical', 'dr_canonical');
add_filter('rank_math/frontend/canonical', 'dr_canonical');
add_filter('aioseo_canonical_url', 'dr_canonical');
function dr_seo_title($value) {
    $page = dr_page();
    $managed = get_post_meta(get_queried_object_id(), '_dr_seo_title', true);
    return $page && !$page['casino'] && $managed !== '' ? $managed : $value;
}
function dr_seo_description($value) {
    $page = dr_page();
    $managed = get_post_meta(get_queried_object_id(), '_dr_description', true);
    return $page && !$page['casino'] && $managed !== '' ? $managed : $value;
}
foreach (array('wpseo_title', 'rank_math/frontend/title', 'aioseo_title') as $hook) { add_filter($hook, 'dr_seo_title', 99); }
add_filter('pre_get_document_title', 'dr_seo_title', 99);
foreach (array('wpseo_metadesc', 'rank_math/frontend/description', 'aioseo_description') as $hook) { add_filter($hook, 'dr_seo_description', 99); }

add_action('wp_head', function() {
    $page = dr_page();
    if (!$page) { return; }
    $seo_plugin = dr_has_seo_plugin();
    if (!$seo_plugin) {
        echo '<link rel="canonical" href="' . esc_url(dr_canonical()) . '">' . "\n";
        $title = get_post_meta(get_queried_object_id(), '_dr_seo_title', true);
        $description = get_post_meta(get_queried_object_id(), '_dr_description', true);
        if ($title !== '') { echo '<title>' . esc_html($title) . '</title>' . "\n"; }
        if ($description !== '') { echo '<meta name="description" content="' . esc_attr($description) . '">' . "\n"; }
    }
}, 1);
add_action('wp', function() {
    if (dr_page()) {
        remove_action('wp_head', 'rel_canonical');
        if (!dr_has_seo_plugin()) { remove_action('wp_head', '_wp_render_title_tag', 1); }
    }
});

add_action('add_meta_boxes_page', function($post) {
    if (!get_post_meta($post->ID, '_dr_key', true)) { return; }
    add_meta_box('dr-seo', 'SEO страницы', function($post) {
        wp_nonce_field('dr_save_seo', 'dr_seo_nonce');
        echo '<p>Canonical: <code>' . esc_html(dr_site()['origin'] . get_post_meta($post->ID, '_dr_route', true)) . '</code></p>';
        echo '<p><label>Title<br><input style="width:100%" name="dr_seo_title" value="' . esc_attr(get_post_meta($post->ID, '_dr_seo_title', true)) . '"></label></p>';
        echo '<p><label>Description<br><textarea style="width:100%" rows="3" name="dr_description">' . esc_textarea(get_post_meta($post->ID, '_dr_description', true)) . '</textarea></label></p>';
        echo '<p>При активном SEO-плагине задавайте метаданные в его полях.</p>';
    }, 'page');
});
add_action('save_post_page', function($id) {
    if (!isset($_POST['dr_seo_nonce']) || !wp_verify_nonce(sanitize_text_field(wp_unslash($_POST['dr_seo_nonce'])), 'dr_save_seo') || !current_user_can('edit_post', $id) || wp_is_post_revision($id) || (defined('DOING_AUTOSAVE') && DOING_AUTOSAVE)) { return; }
    foreach (array('dr_seo_title', 'dr_description') as $field) {
        if (isset($_POST[$field])) { update_post_meta($id, '_' . $field, sanitize_text_field(wp_unslash($_POST[$field]))); }
    }
});
