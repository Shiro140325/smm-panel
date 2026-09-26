// Server-rendered public pages (legal, platform prices): only the theme toggle needs script.
import { initTheme, rememberRef } from "./common.js";
import { icons } from "./icons.js";

initTheme(icons);
rememberRef();
