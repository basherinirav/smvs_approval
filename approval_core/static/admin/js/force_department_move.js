// force_department_move.js - Final force move to Chosen box
django.jQuery(document).ready(function($) {
    function forceMoveToChosen() {
        var available = $('#id_departments_from');
        var chosen = $('#id_departments_to');

        if (available.length === 0 || chosen.length === 0) return;

        // Move any selected options from left to right
        var toMove = available.find('option:selected');
        if (toMove.length > 0) {
            toMove.appendTo(chosen);
            chosen.trigger('change');
            console.log('✅ Department moved to Chosen departments');
        }

        // Also handle if user clicks arrows manually
        $('.selector-add, .selector-remove').on('click', function() {
            setTimeout(forceMoveToChosen, 100);
        });
    }

    // Run multiple times to catch late-loading widget
    forceMoveToChosen();
    setTimeout(forceMoveToChosen, 400);
    setTimeout(forceMoveToChosen, 800);
});