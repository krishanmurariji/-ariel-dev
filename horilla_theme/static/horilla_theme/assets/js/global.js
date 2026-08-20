// CUSTOM MODAL
document.addEventListener("DOMContentLoaded", () => {
  // Open modal
  document.querySelectorAll("[data-modal-open]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const modalId = btn.getAttribute("data-modal-open");
      const modal = document.getElementById(modalId);
      const box = modal.querySelector(".modal-box");

      modal.classList.remove("hidden");
      modal.classList.add("modal-active"); // ✅ Add this
      setTimeout(() => {
        box.classList.remove("opacity-0", "scale-95");
        box.classList.add("opacity-100", "scale-100");
      }, 10);
    });
  });

  // Close modal
  document.querySelectorAll("[data-modal-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const modalId = btn.getAttribute("data-modal-close");
      const modal = document.getElementById(modalId);
      const box = modal.querySelector(".modal-box");

      box.classList.remove("opacity-100", "scale-100");
      box.classList.add("opacity-0", "scale-95");

      setTimeout(() => {
        modal.classList.add("hidden");
        modal.classList.remove("modal-active"); // ✅ Remove here
      }, 300);
    });
  });
});


// CUSTOM DROPDOWN
document.addEventListener("DOMContentLoaded", () => {
  // Toggle dropdowns
  document.querySelectorAll(".dropdown-toggle").forEach((toggle) => {
    toggle.addEventListener("click", function (e) {
      e.stopPropagation();
      // Close other dropdowns
      document.querySelectorAll(".dropdown-menu").forEach((menu) => {
        if (!menu.closest(".dropdown-wrapper").contains(this)) {
          menu.classList.add("hidden");
        }
      });
      // Toggle this one
      const menu =
        this.closest(".dropdown-wrapper").querySelector(".dropdown-menu");
      menu.classList.toggle("hidden");
    });
  });

  // Close dropdowns when clicking outside
  document.addEventListener("click", () => {
    document.querySelectorAll(".dropdown-menu").forEach((menu) => {
      menu.classList.add("hidden");
    });
  });
});

// SIDEBARModal DSESIGN
// Delegated on document (not bound per-button at DOMContentLoaded) because
// pages are swapped in later via htmx navigation (hx-get + hx-push-url) —
// a listener attached only to buttons present at the first page load would
// never fire for a .toggleSidemenu/.closeSidemenu button that arrives in a
// later htmx-swapped page, only working again after a full refresh.
document.addEventListener("click", (e) => {
  const openButton = e.target.closest(".toggleSidemenu");
  if (openButton) {
    const sidebar = document.getElementById(openButton.getAttribute("data-sidebar"));
    if (sidebar) {
      sidebar.classList.toggle("active");
      document.body.classList.toggle("overflow-hidden");
    }
    return;
  }

  const closeButton = e.target.closest(".closeSidemenu");
  if (closeButton) {
    const sidebar = document.getElementById(closeButton.getAttribute("data-sidebar"));
    if (sidebar) {
      sidebar.classList.remove("active");
      document.body.classList.remove("overflow-hidden");
    }
  }
});
