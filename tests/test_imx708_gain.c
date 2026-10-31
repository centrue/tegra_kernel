#include <assert.h>
#include <media/imx708.h>

int main(void)
{
	assert(imx708_gain_code(32, 16) == 512);
	assert(imx708_gain_code(64, 16) == 768);
	assert(imx708_gain_code(128, 16) == 896);
	assert(imx708_gain_code(256, 16) == 960);
	assert(imx708_gain_code(18, 16) == 114);
	assert(imx708_gain_code(16, 16) == 112);
	assert(imx708_gain_code(512, 16) == 960);
	assert(imx708_gain_code(0, 16) == 112);
	assert(imx708_gain_code(32, 0) == 112);
	return 0;
}
