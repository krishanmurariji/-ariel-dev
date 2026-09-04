$(document).ready(function () {
    $('#id_cyclic_feedback').change()
    $("#id_period").on("change", function () {
        period_id = $(this).val();
        if (period_id === "create_new_period") {
            $.ajax({
                type: "GET",
                url: "create-period",
                success: function (response) {
                    $("#PeriodModal").addClass("oh-modal--show");
                    $("#periodModalTarget").html(response);
                },
            });
        }
    });

    $("#id_quick_period").on("change", function () {
        var days = $(this).val();
        if (days === "") { return; }
        var start = new Date();
        var end = new Date();
        end.setDate(end.getDate() + parseInt(days, 10));

        function toInputDate(d) {
            var m = ("0" + (d.getMonth() + 1)).slice(-2);
            var day = ("0" + d.getDate()).slice(-2);
            return d.getFullYear() + "-" + m + "-" + day;
        }

        $("#id_start_date").val(toInputDate(start));
        $("#id_end_date").val(toInputDate(end));
    });
});

function changeCyclicFeedback(element) {
    if (element.checked) {
        $("#cyclic_feedback_period").show();
        $("#id_cyclic_feedback_days_count").attr("required", true);
        $("#id_cyclic_feedback_period").attr("required", true);
    } else {
        $("#cyclic_feedback_period").hide();
        $("#id_cyclic_feedback_days_count").attr("required", false);
        $("#id_cyclic_feedback_period").attr("required", false);
    }
}
