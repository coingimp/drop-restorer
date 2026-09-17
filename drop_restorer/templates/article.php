<?php
if (!defined('ABSPATH')) { exit; }

// Presentation only: never rewrite saved Gutenberg blocks or the owner's text.
function dr_article_h1_count($content) {
    $count = 0;
    if (class_exists('WP_HTML_Tag_Processor')) {
        $tags = new WP_HTML_Tag_Processor($content);
        while ($tags->next_tag('H1')) { $count++; }
    } else {
        $count = preg_match_all('/<h1(?:\s|>)/i', $content);
    }
    return $count;
}
function dr_article_title($content) {
    return dr_article_h1_count($content) ? '' : '<h1 class="dr-article-title">' . esc_html(get_the_title()) . '</h1>';
}

add_action('after_setup_theme', function() {
    add_theme_support('editor-styles');
    add_editor_style('assets/dr-article-editor.css');
});

add_action('wp_enqueue_scripts', function() {
    $page = dr_page();
    if ($page && !$page['casino']) { return; }
    wp_enqueue_style('dr-article', get_template_directory_uri() . '/assets/dr-article.css', array(), '0.1.2');
    wp_enqueue_script('dr-article', get_template_directory_uri() . '/assets/dr-article.js', array(), '0.1.2', true);
});
