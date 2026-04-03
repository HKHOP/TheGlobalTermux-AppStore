const searchInput = document.getElementById("searchInput");
const categorySelect = document.getElementById("categorySelect");
const packageGrid = document.getElementById("packageGrid");
const resultCount = document.getElementById("resultCount");
const packageCardTemplate = document.getElementById("packageCardTemplate");

async function fetchPackages() {
  const params = new URLSearchParams();
  const query = searchInput.value.trim();
  const category = categorySelect.value.trim();

  if (query) {
    params.set("q", query);
  }

  if (category) {
    params.set("category", category);
  }

  const response = await fetch(`/api/packages?${params.toString()}`);
  const data = await response.json();
  return data.items;
}

function renderPackages(items) {
  packageGrid.innerHTML = "";
  resultCount.textContent = `${items.length} package${items.length === 1 ? "" : "s"}`;

  if (!items.length) {
    const emptyState = document.createElement("div");
    emptyState.className = "empty-state";
    emptyState.textContent = "No packages matched your search yet.";
    packageGrid.appendChild(emptyState);
    return;
  }

  for (const pkg of items) {
    const fragment = packageCardTemplate.content.cloneNode(true);
    fragment.querySelector(".category").textContent = pkg.category;
    fragment.querySelector(".name").textContent = pkg.name;
    fragment.querySelector(".description").textContent = pkg.description;
    fragment.querySelector(".install-command").textContent = pkg.installCommand;

    const homepage = fragment.querySelector(".homepage");
    homepage.href = pkg.homepage;
    homepage.textContent = `${pkg.source}`;

    const tagsContainer = fragment.querySelector(".tags");
    for (const tag of pkg.tags) {
      const tagElement = document.createElement("span");
      tagElement.textContent = tag;
      tagsContainer.appendChild(tagElement);
    }

    const copyButton = fragment.querySelector(".copy-button");
    copyButton.addEventListener("click", async () => {
      await navigator.clipboard.writeText(pkg.installCommand);
      copyButton.textContent = "Copied";
      setTimeout(() => {
        copyButton.textContent = "Copy";
      }, 1200);
    });

    packageGrid.appendChild(fragment);
  }
}

async function loadCategories() {
  const response = await fetch("/api/packages");
  const data = await response.json();
  const categories = [...new Set(data.items.map((item) => item.category))].sort();

  for (const category of categories) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    categorySelect.appendChild(option);
  }
}

async function refresh() {
  const items = await fetchPackages();
  renderPackages(items);
}

searchInput.addEventListener("input", refresh);
categorySelect.addEventListener("change", refresh);

loadCategories().then(refresh);
