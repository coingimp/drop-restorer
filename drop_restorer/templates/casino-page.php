<?php
/* Template Name: Casino Page */
if (!defined('ABSPATH')) { exit; }
get_header();
while (have_posts()) {
    the_post();
    $key = dr_key();
    if (preg_match('/^[a-f0-9]{16}$/', $key)) {
        $file = get_template_directory() . '/views/' . $key . '.html';
        if (is_file($file)) {
            ob_start();
            the_content();
            $content = ob_get_clean();
            $shell = dr_asset_urls(file_get_contents($file));
            $shell = str_replace('DR_ARTICLE_HEADING_SLOT', dr_article_title($content), $shell);
            $parts = explode('DR_EDITOR_CONTENT_SLOT', $shell, 2);
            echo $parts[0];
            echo $content;
            if (isset($parts[1])) { echo $parts[1]; }
        }
    }
}
get_footer();
