# Third-Party Notices

The `sim_core/` module in this crate originates from the
[rocket-sim](https://github.com/ZenAlexa/rocket-sim) project (MIT licensed),
which has been physically merged into `l2_engine` as native modules.

The exact upstream revision imported is not yet recoverable from the retained
workspace metadata. The port entered this repository in commit
`f1829a6a87bbbf28e85545ada9b8ad6c17cb661a`. Preserve this notice until the
upstream revision is reconstructed.

## OpenRocket-derived implementation

`barrowman.rs` and `sim_core/sim/adaptive.rs` contain modified ports of
OpenRocket implementation details. The project is therefore distributed under
GPL-3.0-or-later. OpenRocket attribution, source revision, modification notice,
and its full license/additional permission are recorded in the repository
`NOTICE` and `licenses/OpenRocket-LICENSE.txt`.

## rocket-sim License (MIT)

MIT License

Copyright (c) 2025 ZenAlexa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
