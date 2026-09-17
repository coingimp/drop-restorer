<?php
if (!defined('ABSPATH')) { exit; }
$dr_page = dr_page();
?><!doctype html>
<html lang="<?php echo esc_attr(dr_site()['lang']); ?>">
<head>
<?php
if ($dr_page) {
    remove_action('wp_head', 'rel_canonical');
    if (!dr_has_seo_plugin()) { remove_action('wp_head', '_wp_render_title_tag', 1); }
    echo dr_asset_urls($dr_page['head']);
} else { echo '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'; }
wp_head();
if ($dr_page && !empty($dr_page['casino'])) { echo dr_casino_layout_css(); }
?>
</head>
<body<?php
if ($dr_page) {
    foreach ($dr_page['body_attrs'] as $name => $value) {
        if (preg_match('/^(?:class|id|style|dir|data-[a-z0-9_-]+)$/i', $name)) {
            echo ' ' . esc_attr($name) . '="' . esc_attr(is_array($value) ? implode(' ', $value) : $value) . '"';
        }
    }
}
?>>
<?php wp_body_open(); ?>
