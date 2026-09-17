<?php
if (!defined('ABSPATH')) { exit; }
if (!have_posts()) {
    include get_template_directory() . '/404.php';
    return;
}
get_header();
echo '<main class="dr-casino-content">';
while (have_posts()) {
    the_post();
    ob_start(); the_content(); $content = ob_get_clean();
    echo dr_article_title($content) . '<article class="dr-article">' . $content . '</article>';
}
echo '</main>';
get_footer();
