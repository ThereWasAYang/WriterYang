    const savedProjectParentPath = localStorage.getItem("writeryang.projectParentPath");
    $("projectParentPath").dataset.usesRuntimeDefault = savedProjectParentPath ? "0" : "1";
    $("projectParentPath").value = savedProjectParentPath || defaultProjectParentPath;
    $("projectParentPath").addEventListener("input", () => {
      $("projectParentPath").dataset.usesRuntimeDefault = "0";
      localStorage.setItem("writeryang.projectParentPath", $("projectParentPath").value);
      updateProjectInitPathPreview();
    });
    $("projectParentPath").addEventListener("change", () => {
      $("projectParentPath").dataset.usesRuntimeDefault = "0";
      localStorage.setItem("writeryang.projectParentPath", $("projectParentPath").value);
      updateProjectInitPathPreview();
    });
    $("projectTitle").addEventListener("input", updateProjectInitPathPreview);
    $("projectTitle").addEventListener("change", updateProjectInitPathPreview);
    $("debugOptionsDetails").addEventListener("toggle", () => window.requestAnimationFrame(syncWorkbenchStickyOffset));
    window.addEventListener("resize", syncWorkbenchStickyOffset);
    if (window.ResizeObserver) {
      const stickyResizeObserver = new ResizeObserver(() => syncWorkbenchStickyOffset());
      const header = document.querySelector(".app-header");
      if (header) stickyResizeObserver.observe(header);
      stickyResizeObserver.observe($("workbenchCommandBar"));
    }
    applyEmbeddingProviderDefaults("setup", false);
    applyEmbeddingProviderDefaults("config", false);
    updateProjectInitPathPreview();
    syncWorkbenchStickyOffset();
    loadRuntime();
