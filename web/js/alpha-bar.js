(function(){
  var bar = document.querySelector(".alpha-bar");
  if (!bar) return;
  bar.addEventListener("click", function(e) {
    var link = e.target.closest("[data-letter]");
    if (!link) return;
    e.preventDefault();
    var anchor = document.getElementById("letter-" + link.dataset.letter);
    if (!anchor) return;
    var scroller = document.querySelector(".people-browser");
    if (!scroller) return;
    var barH = bar.offsetHeight + 16;
    var top = anchor.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - barH;
    scroller.scrollTo({top: top, behavior: "smooth"});
  });
})();
