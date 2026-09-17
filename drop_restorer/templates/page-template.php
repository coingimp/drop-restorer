<?php
/* Template Name: Restored Page */
if (!defined('ABSPATH')) { exit; }
get_header();
while (have_posts()) {
    the_post();
    // Archived HTML already includes its original layout and navigation.
    echo dr_asset_urls(get_the_content());
}
get_footer();
